"""
EnvyUI  v1.0.5
==============
A self-contained Windows launcher for the envied download engine.

Calls envied directly via: uv run envied dl SERVICE URL [options]
Envied is bundled in the EnvyCore/packages folder.

REQUIREMENTS
------------
Windows 10/11 (64-bit), Python 3.12+ from python.org.
Everything else is installed automatically.
"""

import os
import ctypes
import sys
import json

# Tell Windows this process owns the "EnvyUI" identity so the taskbar button
# shows the EnvyUI icon and "Pin to taskbar" pins EnvyUI rather than pythonw.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TwinVine.EnvyUI")
except Exception:
    pass

# Force Chromium software rendering for VM compatibility and to prevent the
# NVIDIA GeForce Experience overlay from triggering on GPU machines.
# Has zero visible impact on a text terminal.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")


# When running as a PyInstaller frozen exe, ensure stdlib modules are findable
# by venv packages (e.g. rich needs colorsys which may not be in the frozen bundle)
if getattr(sys, 'frozen', False):
    import sysconfig
    _stdlib = sysconfig.get_path('stdlib')
    if _stdlib and _stdlib not in sys.path:
        sys.path.insert(0, _stdlib)
    # Also add the system Python Lib folder as fallback
    import pathlib as _pl
    for _candidate in [
        _pl.Path(sys.executable).parent / 'Lib',
        _pl.Path(sys.executable).parent.parent / 'Lib',
    ]:
        if _candidate.exists() and str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
import subprocess
import threading
import shutil
import webbrowser
import ssl
from pathlib import Path
from datetime import datetime

# ── SSL: lazy certifi lookup so fresh installs work even before uv sync runs ──
def _make_ssl_ctx():
    _pem = Path(__file__).parent / "EnvyCore" / ".venv" / "Lib" / "site-packages" / "certifi" / "cacert.pem"
    if _pem.exists():
        return ssl.create_default_context(cafile=str(_pem))
    try:
        import certifi as _c
        return ssl.create_default_context(cafile=_c.where())
    except Exception:
        return ssl.create_default_context()
ssl._create_default_https_context = _make_ssl_ctx


# ── PyQt6 ─────────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QFrame, QCheckBox, QComboBox, QSlider,
    QTextEdit, QScrollArea, QDialog, QDialogButtonBox, QListWidget,
    QListWidgetItem, QAbstractItemView, QSplitter, QStackedWidget,
    QProgressBar, QPlainTextEdit, QMessageBox, QFileDialog, QInputDialog, QTabWidget,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QProcess, QTimer, QSize,
)
from PyQt6.QtGui import QPalette, QColor, QFont, QTextCursor, QPainter, QFontMetrics
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings


class _TermView(QWebEngineView):
    """QWebEngineView embedding xterm.js; bytes pushed via runJavaScript (no WebSocket)."""

    _XTERM_HTML_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:100%; height:100%; background:{bg}; overflow:hidden; max-width:100%; }}
  #terminal {{ width:100%; height:100%; overflow:hidden; }}
  .xterm {{ max-width:100% !important; }}
  .xterm-viewport {{ overflow-y:auto !important; overflow-x:hidden !important; }}
  .xterm-screen {{ max-width:100% !important; }}
  .xterm-viewport::-webkit-scrollbar {{ width:6px; }}
  .xterm-viewport::-webkit-scrollbar-track {{ background:{bg}; }}
  .xterm-viewport::-webkit-scrollbar-thumb {{ background:{scroll_thumb}; border-radius:3px; }}
  .xterm-viewport::-webkit-scrollbar-thumb:hover {{ background:{fg}; }}
</style>
<link rel="stylesheet" href="{xterm_css}"/>
<script src="{xterm_js}"></script>
</head>
<body>
<div id="terminal"></div>
<script>
var term = new Terminal({{
  fontFamily: 'Consolas, "Cascadia Code", monospace',
  fontSize: 11,
  theme: {{ background: '{bg}', foreground: '{fg}', cursor: '{fg}' }},
  convertEol: false,
  scrollback: 5000,
  disableStdin: true,
  cols: 120,
  rows: 32,
}});
var termEl = document.getElementById('terminal');
term.open(termEl);
function _fitTerm() {{
  var h = termEl.clientHeight;
  var w = termEl.clientWidth;
  if (h <= 0 || w <= 0) return;
  var cellH = term._core._renderService.dimensions.css.cell.height || 17;
  var cellW = term._core._renderService.dimensions.css.cell.width  || 7;
  var newRows = Math.max(4, Math.floor(h / cellH));
  var newCols = Math.max(40, Math.floor(w / cellW));
  if (newRows !== term.rows || newCols !== term.cols) {{
    term.resize(newCols, newRows);
  }}
}}
var _ro = new ResizeObserver(function() {{ _fitTerm(); }});
_ro.observe(termEl);
setTimeout(_fitTerm, 100);
window._envyWrite = function(b64) {{
  var bytes = Uint8Array.from(atob(b64), function(c) {{ return c.charCodeAt(0); }});
  term.write(bytes, function() {{ term.scrollToBottom(); }});
}};
window._envyReset = function() {{
  term.reset();
}};
</script>
</body></html>"""

    def __init__(self, bg: str, fg: str, scroll_thumb: str = "#444", parent=None):
        super().__init__(parent)
        self._bg = bg
        self._fg = fg
        self._scroll_thumb = scroll_thumb
        self._page_ready = False
        self._pending: list[bytes] = []   # written before page loads
        self._batch:   list[bytes] = []   # 50ms write batching
        self._batch_pending = False
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.page().setBackgroundColor(QColor(bg))
        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.loadFinished.connect(self._on_load_finished)
        self._load_page()

    def _on_load_finished(self, ok: bool):
        # Set ready regardless of ok — _envyWrite guard handles missing xterm.js gracefully
        self._page_ready = True
        if self._pending:
            import base64 as _b64
            combined = b''.join(self._pending)
            self._pending.clear()
            b64 = _b64.b64encode(combined).decode()
            self.page().runJavaScript(f"window._envyWrite && window._envyWrite('{b64}')")

    def _load_page(self):
        from pathlib import Path as _Path
        from PyQt6.QtCore import QUrl
        _base = "https://cdn.jsdelivr.net/npm/xterm@5.3.0"
        _asset_dir = _Path(__file__).parent / "EnvyCore" / "assets" / "xterm"
        _asset_dir.mkdir(parents=True, exist_ok=True)
        _js_local  = _asset_dir / "xterm.min.js"
        _css_local = _asset_dir / "xterm.min.css"
        # Use relative paths when local files exist so the HTML loads from the
        # same directory — avoids cross-origin issues loading file:// from setHtml.
        xterm_js  = "xterm.min.js"  if _js_local.exists()  else f"{_base}/lib/xterm.min.js"
        xterm_css = "xterm.min.css" if _css_local.exists() else f"{_base}/css/xterm.min.css"
        html = self._XTERM_HTML_TMPL.format(
            bg=self._bg, fg=self._fg, scroll_thumb=self._scroll_thumb,
            xterm_js=xterm_js, xterm_css=xterm_css)
        # Write HTML to the asset directory so relative script paths resolve correctly,
        # then load it as a local file (same origin — no cross-origin restrictions).
        _html_path = _asset_dir / "_terminal.html"
        _html_path.write_text(html, encoding="utf-8")
        self.load(QUrl.fromLocalFile(str(_html_path)))

    def reset_terminal(self):
        self._pending.clear()
        self._batch.clear()
        if self._page_ready:
            self.page().runJavaScript("window._envyReset && window._envyReset()")

    def write_text(self, text: str):
        self.write_bytes((text + '\r\n').encode('utf-8', errors='replace'))

    def write_bytes(self, data: bytes):
        if not self._page_ready:
            self._pending.append(data)
            return
        self._batch.append(data)
        if not self._batch_pending:
            self._batch_pending = True
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, self._flush_batch)

    def _flush_batch(self):
        self._batch_pending = False
        if not self._batch:
            return
        import base64 as _b64
        combined = b''.join(self._batch)
        self._batch.clear()
        b64 = _b64.b64encode(combined).decode()
        self.page().runJavaScript(f"window._envyWrite && window._envyWrite('{b64}')")

REQUESTS_AVAILABLE = True  # urllib.request is stdlib — always available

# ── Constants ──────────────────────────────────────────────────────────────────

APP_NAME        = "EnvyUI"
APP_VERSION     = "1.0.5"
GITHUB_REPO     = "Lseauk/EnvyUI"
GITHUB_URL      = f"https://github.com/{GITHUB_REPO}"
LAUNCHER_URL    = "https://github.com/Lseauk/EnvyUI"

# Work out the best default install directory:
#   1. If the launcher lives inside an existing TwinVine checkout, use that.
#   2. If the launcher's own directory looks like a good home, put TwinVine
#      as a sibling folder next to the launcher.
#   3. Fall back to ~/TwinVine.
def _detect_default_install() -> Path:
    # When frozen by PyInstaller sys.executable is the .exe path;
    # when run as a .py file __file__ is the script path.
    if getattr(sys, "frozen", False):
        launcher_dir = Path(sys.executable).resolve().parent
    else:
        launcher_dir = Path(__file__).resolve().parent
    # Check if we're already inside a TwinVine checkout (contains envied)
    for candidate in [launcher_dir, launcher_dir.parent]:
        if (candidate / "packages" / "envied").exists():
            return candidate
    # Otherwise put TwinVine as a sibling of the launcher
    return launcher_dir / "EnvyCore"

DEFAULT_INSTALL = _detect_default_install()
CONFIG_FILE     = Path(os.path.expanduser("~")) / ".envy_launcher.json"

# Catppuccin Mocha palette
C = {
    "bg":           "#1e1e2e",
    "surface":      "#181825",
    "overlay":      "#313244",
    "text":         "#cdd6f4",
    "subtext":      "#a6adc8",
    "pink":         "#f5c2e7",
    "mauve":        "#cba6f7",
    "blue":         "#89b4fa",
    "green":        "#a6e3a1",
    "yellow":       "#f9e2af",
    "red":          "#f38ba8",
    "peach":        "#fab387",
    "border":       "#45475a",
}

# ── Service definitions ──────────────────────────────────────────────────────

CORE_SERVICES = [
    {"id": "ALL4",     "label": "ALL4"},
    {"id": "iP",       "label": "BBC iPlayer"},
    {"id": "ITV",      "label": "ITVX"},
    {"id": "MY5",      "label": "My5"},
    {"id": "UKTV",     "label": "U (UKTV)"},
    {"id": "RTE",      "label": "RTE"},
    {"id": "STV",      "label": "STV"},
    {"id": "TPTV",     "label": "TPTV"},
    {"id": "RKTN",     "label": "Rakuten TV"},
    {"id": "TUBI",     "label": "Tubi"},
    {"id": "PLUTO",    "label": "Pluto TV"},
    {"id": "VM",       "label": "VM Play (IE)"},
    {"id": "TVNZ",     "label": "TVNZ"},
    {"id": "ThreeNow", "label": "ThreeNow (NZ)"},
    {"id": "AUBC",     "label": "ABC iView (AU)"},
    {"id": "SEVEN",    "label": "7plus (AU)"},
    {"id": "NINE",     "label": "9Now (AU)"},
    {"id": "TEN",      "label": "10play (AU)"},
    {"id": "SBS",      "label": "SBS On Demand (AU)"},
    {"id": "ROKU",     "label": "Roku (US)"},
    {"id": "CBS",      "label": "CBS (US)"},
    {"id": "NBC",      "label": "NBC"},
    {"id": "PBS",      "label": "PBS"},
    {"id": "CWTV",     "label": "The CW (US)"},
    {"id": "CRAV",     "label": "Crave"},
    {"id": "CBC",      "label": "CBC Gem"},
    {"id": "PLEX",     "label": "Plex"},
]

# Services that support episode listing via BrowseWorker
BROWSE_SUPPORTED = {"iP", "ALL4", "CBS", "CBC", "CRAV", "CWTV", "ITV", "MY5", "UKTV", "STV", "RTE", "PLUTO", "TUBI", "TVNZ", "NINE", "NBC", "PBS", "NRK", "ARD", "ZDF", "RKTN", "VM", "ROKU", "ThreeNow", "AUBC", "SEVEN", "TEN", "SBS"}

# ── CRAV token cache ─────────────────────────────────────────────────────────
_CRAV_TOKEN_CACHE: dict = {}  # {"token": str, "expires": float}

def _crav_get_graphql_token() -> str | None:
    """Authenticate with Crave and return a base64 graphql token, or None if no credentials."""
    import urllib.request as _ur, urllib.parse as _up, json as _json, base64 as _b64, time as _t, re as _re
    cache = _CRAV_TOKEN_CACHE
    if cache.get("token") and _t.time() < cache.get("expires", 0):
        return cache["token"]

    from pathlib import Path as _Path
    cfg = load_config()
    envied_yaml = _Path(cfg.get("install_dir", "")) / "packages" / "envied" / "src" / "envied" / "envied.yaml"
    if not envied_yaml.exists():
        return None

    text = envied_yaml.read_text(encoding="utf-8")
    m = _re.search(r'^\s*CRAV:\s*(.+)', text, _re.MULTILINE)
    if not m:
        return None
    cred_str = m.group(1).strip()
    # Format is email:password — split on first colon after the @ domain
    colon_idx = cred_str.index(':', cred_str.index('@'))
    username = cred_str[:colon_idx]
    password = cred_str[colon_idx + 1:]

    data = _up.urlencode({
        "grant_type": "password",
        "username":   username,
        "password":   password,
    }).encode()
    req = _ur.Request(
        "https://account.bellmedia.ca/api/login/v2.2",
        data=data,
        headers={
            "User-Agent":    "Dalvik/2.1.0 (Linux; U; Android 11; SHIELD Android TV Build/RQ1A.210105.003)",
            "Authorization": "Basic Y3JhdmUtYW5kcm9pZDpkZWZhdWx0",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
    )
    with _ur.urlopen(req, timeout=20) as r:
        tokens = _json.loads(r.read().decode())

    access_token = tokens["access_token"]
    graphql_token = _b64.b64encode(_json.dumps({
        "platform":    "platform_androidtv",
        "accessToken": access_token,
    }).encode()).decode()

    cache["token"]   = graphql_token
    cache["expires"] = _t.time() + tokens.get("expires_in", 3600) - 30
    return graphql_token


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    d = {"install_dir": str(DEFAULT_INSTALL), "installed": False,
         "last_commit": None, "install_date": None}
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            # Only trust a saved install_dir if it actually exists on this
            # machine — prevents stale paths from a different PC breaking things.
            saved_dir = saved.get("install_dir", "")
            if saved_dir and not Path(saved_dir).exists():
                saved.pop("install_dir", None)
                saved["installed"] = False   # force re-install on new machine
            d.update(saved)
        except Exception:
            pass
    return d

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Envied state ──────────────────────────────────────────────────────────────

_VF_LOADED = True  # envied is always bundled — no runtime bootstrap needed

# ── Qt selection dialogs ───────────────────────────────────────────────────────

class SingleSelectDialog(QDialog):
    """Replace beaupy.select() — pick exactly one item from a list."""

    def __init__(self, items: list, title="Select", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 400)
        self._result = None
        self._apply_mocha(self)

        layout = QVBoxLayout(self)
        lbl = QLabel("Select one item:")
        lbl.setStyleSheet(f"color:{C['subtext']};")
        layout.addWidget(lbl)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listw.setStyleSheet(f"""
            QListWidget {{background:{C['surface']};color:{C['text']};
                          border:1px solid {C['border']};font-size:12px;}}
            QListWidget::item:selected {{background:{C['green']};color:{C['bg']};}}
            QListWidget::item:hover {{background:{C['overlay']};}}
        """)
        for item in items:
            self.listw.addItem(str(item))
        if items:
            self.listw.setCurrentRow(0)
        self.listw.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.listw)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.setStyleSheet(f"color:{C['text']};background:{C['overlay']};")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def accept(self):
        sel = self.listw.selectedItems()
        if sel:
            self._result = sel[0].text()
        super().accept()

    def result_item(self):
        return self._result

    @staticmethod
    def _apply_mocha(w):
        w.setStyleSheet(f"background:{C['bg']};color:{C['text']};")


class MultiSelectDialog(QDialog):
    """Replace beaupy.select_multiple() — pick one or more items."""

    def __init__(self, items: list, title="Select episodes", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(620, 500)
        self._results = []
        self._apply_mocha(self)

        layout = QVBoxLayout(self)
        lbl = QLabel("Select one or more items  (Ctrl+click for multiple):")
        lbl.setStyleSheet(f"color:{C['subtext']};")
        layout.addWidget(lbl)

        # Quick select buttons
        btn_row = QHBoxLayout()
        for label, slot in [("Select All", self._sel_all),
                             ("Clear All",  self._sel_none)]:
            b = QPushButton(label)
            b.setStyleSheet(f"""QPushButton{{background:{C['overlay']};color:{C['text']};
                border:none;padding:4px 10px;border-radius:3px;}}
                QPushButton:hover{{background:{C['green']};color:{C['bg']};}}""")
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.listw = QListWidget()
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listw.setStyleSheet(f"""
            QListWidget {{background:{C['surface']};color:{C['text']};
                          border:1px solid {C['border']};font-size:12px;}}
            QListWidget::item:selected {{background:{C['green']};color:{C['bg']};}}
            QListWidget::item:hover {{background:{C['overlay']};}}
        """)
        for item in items:
            self.listw.addItem(str(item))
        layout.addWidget(self.listw)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.setStyleSheet(f"color:{C['text']};background:{C['overlay']};")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _sel_all(self):
        self.listw.selectAll()

    def _sel_none(self):
        self.listw.clearSelection()

    def accept(self):
        self._results = [i.text() for i in self.listw.selectedItems()]
        super().accept()

    def result_items(self) -> list:
        return self._results

    @staticmethod
    def _apply_mocha(w):
        w.setStyleSheet(f"background:{C['bg']};color:{C['text']};")




_main_window = None




def _launch_all_powershell(episode_list):
    """
    Run all episode commands sequentially, capturing output and displaying
    it in the app's download panel via signals. No console window opens.
    episode_list: list of (command, cwd, slow, slow_min, slow_max) tuples
    """
    import re as _re
    import threading as _th

    if not episode_list:
        return

    # Strip ANSI escape codes
    _ansi = _re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\][^\x07]*\x07|\r')

    def _strip(line: str) -> str:
        return _ansi.sub('', line).strip()

    # Traceback box suppression — buffer lines while inside a rich panel box
    # (detected by "Traceback (most recent call last)" appearing in any line),
    # then drop the whole box only if it's the known UHD/HLG error handled by
    # the retry. All other tracebacks are flushed through intact.
    _tb_box_buf = []
    _tb_in_box  = [False]

    def _emit_line(line, _w, _lf):
        # Detect box start: rich wraps tracebacks in a panel whose first │ line
        # contains "Traceback (most recent call last)"
        if 'Traceback (most recent call last)' in line:
            _tb_in_box[0] = True
            _tb_box_buf.clear()
            _tb_box_buf.append(line)
            return

        if _tb_in_box[0]:
            _tb_box_buf.append(line)
            # Box lines start with │, ┌, or └; anything else means the box ended
            # on the previous line and this is the first post-box line
            if not line.startswith(('│', '┌', '└', '╭', '╰')):
                _tb_in_box[0] = False
                combined = '\n'.join(_tb_box_buf)
                _tb_box_buf.clear()
                # Use short prefix that survives rich's truncation
                is_uhd = ('NoStreamsAvailableError' in combined
                          or 'Selection unavailable' in combined)
                if not is_uhd:
                    # Different error — flush the buffered box then emit this line
                    for ln in combined.splitlines():
                        if _w: _w._dl_signals.line.emit(ln)
                        _lf(f"[dl] {ln}")
                    if _w: _w._dl_signals.line.emit(line)
                    _lf(f"[dl] {line}")
                # UHD box: silently drop; also drop the summary error line that
                # follows ("NoStreamsAvailableError: Selection unavailable in UHD.")
                # because the retry banner already covers it
            return

        if _w:
            _w._dl_signals.line.emit(line)
        _lf(f"[dl] {line}")

    # Try to extract percentage from progress lines
    _pct_re = _re.compile(r'(\d{1,3})%')

    cwd = episode_list[0][1]
    total = len(episode_list)

    def _resolve_exe(name: str) -> str:
        venv_scripts = Path(cwd) / ".venv" / "Scripts"
        try:
            saved_cfg = load_config()
            saved = saved_cfg.get("uv_exe") or ""
            if saved and name.lower() in Path(saved).name.lower():
                if Path(saved).exists():
                    return saved
        except Exception:
            pass
        p = venv_scripts / (name + ".exe")
        if p.exists():
            return str(p)
        import shutil as _sh
        hit = _sh.which(name)
        if hit:
            return hit
        for d in [
            Path(os.path.expanduser("~")) / ".local" / "bin",
            Path(os.environ.get("APPDATA", "")) / "uv" / "bin",
        ]:
            if (d / (name + ".exe")).exists():
                return str(d / (name + ".exe"))
        return name

    def _run():
        w = _main_window
        _all_ok = True
        _cancelled = False
        for i, (cmd, ep_cwd, slow_mode, slow_min, slow_max) in enumerate(episode_list, 1):
            resolved = [_resolve_exe(cmd[0])] + list(cmd[1:])
            label = f"Episode {i} of {total}"
            _log_fn(f"[download] Starting {label}: {' '.join(resolved[:4])}...")
            if w:
                w._dl_signals.episode.emit(label)
                w._dl_signals.progress.emit(0)
                w._dl_signals.line.emit(f"─── {label} ───")

            env = os.environ.copy()
            # Clear PyInstaller env vars so uv spawns the real Python,
            # not the frozen bundle's _MEIPASS temp dir (causes 0xC000007B)
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            # Strip _MEIPASS from PATH — PyInstaller injects it so bundled DLLs
            # are found by child processes, but it causes STATUS_INVALID_IMAGE_FORMAT
            # (0xC000007B) when external exes like uv.exe load their own DLLs.
            _meipass = getattr(sys, "_MEIPASS", None)
            if _meipass:
                env["PATH"] = ";".join(
                    p for p in env.get("PATH", "").split(";") if p != _meipass
                )
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONUTF8"]       = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONWARNINGS"]   = "ignore"
            env["WT_SESSION"]        = env.get("WT_SESSION") or "EnvyUI"
            env["FORCE_COLOR"]       = "1"
            env["COLORTERM"]         = "truecolor"
            env["TERM"]              = "xterm-256color"
            # Add tools to PATH
            tools_dirs = [
                str(Path(ep_cwd) / ".venv" / "Scripts"),
                r"C:\Tools\bin",
                r"C:\Program Files\MKVToolNix",
            ]
            env["PATH"] = ";".join(tools_dirs) + ";" + env.get("PATH", "")

            try:
                from winpty import PtyProcess as _PtyProcess
                _use_pty = True
            except ImportError:
                _use_pty = False

            # Debug log — captures all envied output to a plain text file so
            # we can diagnose issues even when the terminal panel doesn't render.
            import time as _time_mod
            _dbg_log_path = Path(ep_cwd) / f"envy_debug_{int(_time_mod.time())}.log"
            _dbg_log = open(_dbg_log_path, "w", encoding="utf-8", errors="replace")
            _dbg_log.write(f"PTY mode: {_use_pty}\nCommand: {resolved}\nCWD: {ep_cwd}\n---\n")
            _dbg_log.flush()

            try:
                for _attempt_uhd in range(2):
                    if _use_pty:
                        # Use a real PTY so Rich emits full colour + cursor-up animation
                        _pty_cmd = ' '.join(
                            f'"{a}"' if ' ' in a else a for a in resolved)
                        proc = _PtyProcess.spawn(
                            _pty_cmd,
                            cwd=ep_cwd,
                            env=env,
                            dimensions=(32, 120),
                        )
                        # Wrap PTY in a duck-typed object compatible with the rest of the loop
                        class _PtyWrapper:
                            def __init__(self, p):
                                self._p = p
                                self.stdout = self
                                self.stdin  = self
                                self.returncode = None
                                self._eof = False
                                # Expose PID so _dl_cancel can use it for taskkill
                                try:
                                    self.pid = p.pid
                                except Exception:
                                    self.pid = None
                            def read(self, n):
                                if self._eof:
                                    return b''
                                try:
                                    data = self._p.read(n)
                                    if data is None or data == '':
                                        self._eof = True
                                        return b''
                                    return data.encode('utf-8', errors='replace')
                                except EOFError:
                                    self._eof = True
                                    return b''
                                except Exception:
                                    self._eof = True
                                    return b''
                            def write(self, data):
                                try:
                                    self._p.write(data.decode('utf-8', errors='replace'))
                                except Exception:
                                    pass
                            def terminate(self):
                                try: self._p.terminate()
                                except Exception: pass
                            def wait(self, timeout=None):
                                try:
                                    rc = self._p.wait()
                                    self.returncode = rc if isinstance(rc, int) else 0
                                except Exception:
                                    self.returncode = 0
                            def poll(self):
                                try:
                                    if not self._p.isalive():
                                        rc = self._p.exitstatus
                                        self.returncode = rc if isinstance(rc, int) else 0
                                        return self.returncode
                                except Exception:
                                    pass
                                return None
                            @property
                            def _handle(self): return 0
                        proc = _PtyWrapper(proc)
                    else:
                        proc = subprocess.Popen(
                            resolved,
                            cwd=ep_cwd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            env=env,
                            text=False,
                            bufsize=0,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                        )

                    # Assign proc to a Windows Job Object with KILL_ON_JOB_CLOSE so
                    # N_m3u8DL-RE and any other grandchildren are guaranteed to die
                    # when the job handle is closed — even if the launcher itself exits.
                    _job_handle = None
                    try:
                        import ctypes, ctypes.wintypes as _wt
                        _kernel = ctypes.windll.kernel32
                        _job_handle = _kernel.CreateJobObjectW(None, None)
                        if _job_handle:
                            # KILL_ON_JOB_CLOSE = 0x2000
                            class _JBELI(ctypes.Structure):
                                _fields_ = [("LimitFlags", ctypes.c_ulong),
                                            ("reserved", ctypes.c_byte * 60)]
                            info = _JBELI()
                            info.LimitFlags = 0x2000
                            _kernel.SetInformationJobObject(
                                _job_handle, 9,  # JobObjectExtendedLimitInformation
                                ctypes.byref(info), ctypes.sizeof(info))
                            _kernel.AssignProcessToJobObject(_job_handle, int(proc._handle))
                    except Exception:
                        _job_handle = None

                    if w:
                        w._dl_proc = proc
                        w._dl_job = _job_handle
                        w._dl_cwd = ep_cwd
                        w._dl_cancelled = False

                    last_pct = 0

                    # ── Shared state for ticker and reader ────────────────────
                    _stage             = ['download']
                    _active            = [True]
                    _mux_start         = [0.0]
                    _last_output_time  = [0.0]
                    _all_tracks_done   = [False]   # True once all \r Downloaded seen
                    _tracks_expected   = [0]
                    _tracks_done_count = [0]

                    # ── Unified activity ticker ───────────────────────────────
                    # Single thread handles all phases via silence detection.
                    # N_m3u8DL-RE buffers all \n output and flushes it at the
                    # end, so \n lines cannot be used for real-time stage timing.
                    # Instead we watch _last_output_time: when pipe goes silent
                    # we know either segment-merge or mux is happening.
                    def _activity_ticker(active, stage, mux_start,
                                         last_output_time, all_tracks_done):
                        import time as _t
                        spinner = ['\u280b','\u2819','\u2839','\u2838','\u283c',
                                   '\u2834','\u2826','\u2827','\u2807','\u280f']
                        _p            = 0
                        _silent_start = [0.0]
                        idx           = 0
                        while active[0]:
                            _t.sleep(1)
                            if not active[0]:
                                break
                            if w and getattr(w, '_dl_cancelled', False):
                                break
                            spin = spinner[idx % len(spinner)]
                            idx += 1
                            now         = _t.time()
                            silent_secs = (now - last_output_time[0]
                                           if last_output_time[0] > 0 else 0)

                            if stage[0] == 'done':
                                break

                            if silent_secs > 3:
                                # Pipe has gone quiet — work is happening silently
                                if _silent_start[0] == 0.0:
                                    _silent_start[0] = now - silent_secs
                                elapsed  = int(now - _silent_start[0])
                                mm, ss   = divmod(elapsed, 60)
                                tstr     = f"{mm}m {ss:02d}s" if mm else f"{ss}s"

                                if all_tracks_done[0]:
                                    # All tracks downloaded — silence = mux phase
                                    if mux_start[0] == 0.0:
                                        mux_start[0] = _silent_start[0]
                                    mux_e    = int(now - mux_start[0])
                                    mm2, ss2 = divmod(mux_e, 60)
                                    mtstr    = f"{mm2}m {ss2:02d}s" if mm2 else f"{ss2}s"
                                    if w and active[0]:
                                        w._dl_signals.status.emit(
                                            f"{spin}  Multiplexing\u2026 {mtstr} elapsed"
                                        )
                                    if _p < 97:
                                        _p = min(93 + int(mux_e / 60), 97)
                                        if w and active[0]:
                                            w._dl_signals.progress.emit(_p)
                                else:
                                    # Tracks still downloading — silence = segment merge
                                    if w and active[0]:
                                        w._dl_signals.status.emit(
                                            f"{spin}  Merging video segments\u2026 {tstr} elapsed"
                                            "  (this can take several minutes for larger files)"
                                        )
                                    if _p < 92:
                                        _p = min(88 + int(elapsed / 30), 92)
                                        if w and active[0]:
                                            w._dl_signals.progress.emit(_p)
                            else:
                                # Pipe is active — normal download animation
                                _silent_start[0] = 0.0
                                if _p < 88 and stage[0] == 'download':
                                    if _p < 30:   _p += 3
                                    elif _p < 70: _p += 2
                                    else:         _p += 1
                                    if w and active[0]:
                                        w._dl_signals.progress.emit(min(_p, 88))

                    _last_output_time[0] = __import__('time').time()
                    _th.Thread(target=_activity_ticker,
                               args=(_active, _stage, _mux_start,
                                     _last_output_time, _all_tracks_done),
                               daemon=True).start()

                    # ── Real-time output streaming ────────────────────────────
                    # \r lines: live progress bars -> parse -> status label
                    # \n lines: milestone output -> log panel
                    # All \n output from N_m3u8DL-RE is buffered and arrives in
                    # one burst at the end, so it cannot be used for timing.
                    import re as _re_dl

                    _ansi_re = _re_dl.compile(
                        r'\x1b\[[0-9;]*[mGKHF]|\x1b\][^\x07]*\x07')
                    _box_re  = _re_dl.compile(
                        r'[\u2500-\u257f\u2580-\u259f\u2190-\u21ff\u23af'
                        r'\u2013\u2014\u2015]')
                    _prog_re = _re_dl.compile(
                        r'\u2022\s*([\d:]+)\s*\u2022\s*(.+)$')

                    def _parse_r_line(raw_r):
                        s = _ansi_re.sub('', raw_r).strip()
                        if 'Multiplexing' in s:
                            return ('mux_progress', None)
                        text = _box_re.sub('', s).strip().lstrip("'").strip()
                        m = _prog_re.search(text)
                        if m:
                            tstr  = m.group(1).strip()
                            stage = m.group(2).strip()
                            if 'Downloaded' in stage:
                                return ('track_done',
                                        f"\u2714  Track downloaded ({tstr})")
                            if 'Merging' in stage:
                                return ('merging',
                                        f"\u23f3  Merging segments\u2026 ({tstr})")
                            if any(x in stage for x in
                                   ('HLS', 'MB/s', 'kb/s', 'Mbps')):
                                return ('downloading',
                                        f"\u23f3  Downloading\u2026 {tstr}  {stage}")
                            return ('other', f"\u23f3  {stage} ({tstr})")
                        if s and not _box_re.search(s):
                            return ('text', s)
                        return (None, None)

                    _rawbuf        = b''
                    _last_r_status = ['']
                    _saw_uhd_error = [False]
                    _mute_bytes    = [False]  # suppress xterm output during UHD traceback

                    # Feed stdout into a queue from a reader thread so the main
                    # loop can use timeouts — needed to detect interactive prompts
                    # (e.g. TVNZ OTP) that don't end with \n and would block forever.
                    import queue as _queue
                    _stdout_q = _queue.Queue()

                    def _stdout_reader(pipe, q):
                        try:
                            while True:
                                chunk = pipe.read(256)
                                if not chunk:
                                    # For PTY wrapper, also check _eof flag
                                    if getattr(pipe, '_eof', False):
                                        break
                                    # For plain pipes, empty read = EOF
                                    if not hasattr(pipe, '_eof'):
                                        break
                                    # PTY returned empty but not yet flagged EOF — brief wait
                                    import time as _t; _t.sleep(0.05)
                                    continue
                                q.put(chunk)
                        except Exception:
                            pass
                        finally:
                            q.put(None)  # sentinel

                    _th.Thread(target=_stdout_reader,
                               args=(proc.stdout, _stdout_q),
                               daemon=True).start()

                    # Patterns that indicate the service is waiting for user input
                    _prompt_re = _re_dl.compile(
                        r'(Enter OTP|enter.{0,20}code|OTP code|Prompt|enter.{0,30}password'
                        r'|enter.{0,30}pin|verification code)',
                        _re_dl.IGNORECASE,
                    )
                    _prompt_stall = [0.0]   # time when buffer last had no newline

                    while True:
                        if w and w._dl_proc is None:
                            _cancelled = True
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            break

                        try:
                            chunk = _stdout_q.get(timeout=0.5)
                        except _queue.Empty:
                            # No new data — check if buffer looks like a prompt.
                            # Require 5s stall (PTY output can pause briefly during
                            # downloads without being an actual interactive prompt).
                            if _rawbuf:
                                partial = _ansi_re.sub(
                                    '', _rawbuf.decode('utf-8', errors='replace')
                                ).strip()
                                if _prompt_re.search(partial):
                                    if _prompt_stall[0] == 0.0:
                                        _prompt_stall[0] = __import__('time').time()
                                    elif __import__('time').time() - _prompt_stall[0] > 5.0:
                                        _prompt_stall[0] = 0.0
                                        _rawbuf = b''
                                        if w:
                                            w._dl_signals.prompt.emit(partial)
                            else:
                                _prompt_stall[0] = 0.0
                            continue
                        _prompt_stall[0] = 0.0
                        if chunk is None:
                            break

                        _last_output_time[0] = __import__('time').time()

                        # Write to debug log
                        try:
                            _dbg_log.write(chunk.decode('utf-8', errors='replace'))
                            _dbg_log.flush()
                        except Exception:
                            pass

                        # Send raw bytes straight to xterm.js — it handles ANSI,
                        # colours, cursor-up redraws, box-drawing, everything.
                        # Mute during a UHD/HLG traceback so it never reaches the
                        # terminal — only applies when a HLG retry is possible.
                        if (not _mute_bytes[0]
                                and b'Traceback' in chunk
                                and _attempt_uhd == 0
                                and '--range' in resolved):
                            _mute_bytes[0] = True
                        if w and not _mute_bytes[0]:
                            w._dl_signals.raw_bytes.emit(chunk)

                        # Scan cleaned lines for milestone keywords so the
                        # progress bar / status / episode labels still update.
                        _rawbuf += chunk
                        while b'\n' in _rawbuf:
                            line_b, _rawbuf = _rawbuf.split(b'\n', 1)
                            clean = _ansi_re.sub(
                                '', line_b.decode('utf-8', errors='replace')
                            ).strip()
                            if not clean:
                                continue

                            if 'Selection unavailable in UHD' in clean:
                                _saw_uhd_error[0] = True

                            _tc = _re_dl.match(
                                r'^(\d+)\s+(Video|Audio|Subtitle)', clean)
                            if _tc:
                                _tracks_expected[0] += int(_tc.group(1))

                            if 'Track downloads finished' in clean:
                                _all_tracks_done[0] = True
                                _stage[0] = 'mux'
                                last_pct  = 90
                                if w:
                                    w._dl_signals.progress.emit(90)
                                    w._dl_signals.status.emit(
                                        "⏳  Track downloads complete…")
                            elif ('Converting Subtitles' in clean
                                  or 'Converting subtitles' in clean):
                                _stage[0] = 'mux'
                                last_pct  = 91
                                if w:
                                    w._dl_signals.progress.emit(91)
                                    w._dl_signals.status.emit(
                                        "⏳  Converting subtitles…")
                            elif ('Title downloaded' in clean
                                  or 'downloaded in' in clean.lower()):
                                _stage[0] = 'done'
                                last_pct  = 97
                                if w:
                                    w._dl_signals.progress.emit(97)
                                    w._dl_signals.status.emit(
                                        "✓  Download complete —"
                                        " finalising…")
                            elif 'Processed all titles' in clean:
                                last_pct = 99
                                if w:
                                    w._dl_signals.progress.emit(99)
                                    w._dl_signals.status.emit("")

                            kind, status_str = _parse_r_line(
                                line_b.decode('utf-8', errors='replace'))
                            if kind == 'track_done':
                                _tracks_done_count[0] += 1
                                if (_tracks_expected[0] > 0
                                        and _tracks_done_count[0]
                                        >= _tracks_expected[0]):
                                    _all_tracks_done[0] = True
                                elif _tracks_done_count[0] >= 3:
                                    _all_tracks_done[0] = True
                            if status_str and status_str != _last_r_status[0]:
                                _last_r_status[0] = status_str
                                if w:
                                    w._dl_signals.status.emit(status_str)

                    if _rawbuf:
                        # Flush any remaining bytes to xterm
                        if w:
                            try:
                                w._dl_signals.raw_bytes.emit(_rawbuf)
                            except Exception:
                                pass
                        try:
                            clean = _ansi_re.sub(
                                '', _rawbuf.decode('utf-8', errors='replace')
                            ).strip()
                            if 'Selection unavailable in UHD' in clean:
                                _saw_uhd_error[0] = True
                        except Exception:
                            pass

                    if _cancelled:
                        _active[0] = False
                        _stage[0]  = 'done'
                        proc.wait()
                        if w:
                            w._dl_signals.status.emit("")
                        break

                    # Keep ticker alive during proc.wait() so mux timer keeps running
                    proc.wait()

                    # If this episode failed because the requested HLG/UHD
                    # stream isn't available for this title, retry once
                    # with --range HLG stripped (falls back to SDR).
                    if (proc.returncode != 0 and _saw_uhd_error[0]
                            and _attempt_uhd == 0 and not _cancelled
                            and '--range' in resolved):
                        try:
                            ridx = resolved.index('--range')
                            if (ridx + 1 < len(resolved)
                                    and resolved[ridx + 1] == 'HLG'):
                                del resolved[ridx:ridx + 2]
                                if w:
                                    _mute_bytes[0] = False
                                    w._dl_signals.line.emit(
                                        "\u26a0  HLG/UHD stream not available "
                                        "for this title \u2014 retrying in SDR\u2026")
                                _log_fn(
                                    "[download] UHD unavailable, "
                                    "retrying without --range HLG")
                                _saw_uhd_error[0] = False
                                _active[0] = False
                                _stage[0]  = 'done'
                                _last_output_time[0] = 0.0
                                continue
                        except ValueError:
                            pass
                    break
                _active[0] = False
                _stage[0]  = 'done'
                try:
                    _dbg_log.write(f"\n--- exit code: {proc.returncode} ---\n")
                    _dbg_log.close()
                except Exception:
                    pass
                if w:
                    w._dl_signals.status.emit("")
                if w and not _cancelled:
                    w._dl_signals.progress.emit(100)
                    if proc.returncode == 0:
                        status = "\u2713 complete"
                    else:
                        status = f"\u2717 failed (code {proc.returncode})"
                        _all_ok = False
                    w._dl_signals.line.emit(f"Episode {i}: {status}")

                # Slow mode delay between episodes
                if slow_mode and i < total and not _cancelled:
                    import random as _random, time as _time_slow
                    delay = _random.randint(slow_min, slow_max)
                    _log_fn(f"[download] Slow mode: waiting {delay}s before next episode...")
                    if w:
                        w._dl_signals.line.emit(
                            f"⏱  Slow mode — waiting {delay}s before next episode...")
                    _time_slow.sleep(delay)

            except Exception as e:
                _all_ok = False
                _log_fn(f"[download] Error: {e}")
                if w:
                    w._dl_signals.line.emit(f"Error: {e}")

        if _cancelled:
            _log_fn("[download] Download cancelled")
            if w:
                w._dl_signals.done.emit(False)
        else:
            _log_fn("[download] All episodes complete")
            if w:
                w._dl_signals.done.emit(_all_ok)

    # Show the download panel on main thread, then start worker thread
    def _show_panel():
        w = _main_window
        if not w:
            return
        w._dl_term.reset_terminal()
        w._dl_progress.setValue(0)
        w._dl_proc = None
        w._dl_ep_label.setText(f"Downloading {total} episode(s)...")
        w._dl_ep_label.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        try:
            w._dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        w._dl_cancel_btn.setText("\u2715  Cancel Download")
        w._dl_cancel_btn.clicked.connect(w._dl_cancel)
        w._dl_panel.setVisible(True)
        w._sel_panel.setVisible(False)
        w._action_widget.setVisible(False)
        w._action_input_widget.setVisible(False)
        w._dl_status.setText("\u23f3 Busy — download in progress")
        w._dl_status.setStyleSheet(
            f"color:{C['yellow']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    _show_panel()
    _th.Thread(target=_run, daemon=True).start()
    return  # return immediately — output comes via signals



def _launch_powershell(command, cwd):
    """Open a single PowerShell window for one download command and WAIT for it to finish."""
    import tempfile, shutil as _shutil

    # Quote each argument for PowerShell (wrap in single-quotes, escape
    # any literal single-quotes inside by doubling them).
    def ps_quote(s):
        return "'" + str(s).replace("'", "''") + "'"

    venv_scripts = Path(cwd) / ".venv" / "Scripts"

    # Resolve the executable (first token, usually "uv") to its absolute path.
    # Priority:
    #   1. Saved uv_exe from config  (most reliable — recorded during install)
    #   2. TwinVine venv Scripts
    #   3. Next to sys.executable (where pip puts it)
    #   4. System PATH
    #   5. uv self-install locations
    def _resolve_exe(name: str) -> str:
        # 1. Config-saved path (set during install, survives restarts)
        try:
            saved_cfg = load_config()
            saved = saved_cfg.get("uv_exe") or ""
            if saved and name.lower() in Path(saved).name.lower():
                if Path(saved).exists():
                    return saved
        except Exception:
            pass
        # 2. venv Scripts
        p = venv_scripts / (name + ".exe")
        if p.exists():
            return str(p)
        # 3. Next to sys.executable and its Scripts subdirectory
        for py_dir in [Path(sys.executable).parent,
                       Path(sys.executable).parent / "Scripts",
                       Path(sys.prefix) / "Scripts",
                       Path(sys.base_prefix) / "Scripts"]:
            c = py_dir / (name + ".exe")
            if c.exists():
                return str(c)
        # 4. System PATH
        import shutil as _sh
        hit = _sh.which(name)
        if hit:
            return hit
        # 5. uv self-install locations
        for d in [
            Path(os.path.expanduser("~")) / ".local" / "bin",
            Path(os.path.expanduser("~")) / ".cargo" / "bin",
            Path(os.environ.get("APPDATA", "")) / "uv" / "bin",
        ]:
            if (d / (name + ".exe")).exists():
                return str(d / (name + ".exe"))
        return name   # last resort

    resolved_command = [_resolve_exe(command[0])] + list(command[1:])
    cmd_ps = " ".join(ps_quote(a) for a in resolved_command)
    _log_fn(f"[download] Resolved exe: {resolved_command[0]}")

    # Tool dirs for PATH inside the PS1 session
    # Install-media-tools.ps1 puts ALL tools into C:\Tools\bin (confirmed from source).
    # MKVToolNix goes to C:\Program Files\MKVToolNix via its own silent installer.
    # We hardcode these and then do a fallback search so it works even if the
    # user moved things.
    tools_bin = Path("C:/Tools/bin")
    mkv_dir   = Path("C:/Tools/bin")  # portable install goes here

    # Fallback: scan inside the TwinVine install dir in case tools ended up there
    def _find_exe(name: str) -> str | None:
        if (tools_bin / name).exists():
            return str(tools_bin)
        if mkv_dir.exists() and (mkv_dir / name).exists():
            return str(mkv_dir)
        try:
            for p in Path(cwd).rglob(name):
                return str(p.parent)
        except Exception:
            pass
        import shutil as _sh2
        hit = _sh2.which(name)
        return str(Path(hit).parent) if hit else None

    nm3u8_dir    = _find_exe("N_m3u8DL-RE.exe") or str(tools_bin)
    ffmpeg_dir   = _find_exe("ffmpeg.exe")       or str(tools_bin)
    mkvmerge_dir = _find_exe("mkvmerge.exe")     or str(mkv_dir)

    _log_fn(f"[download] N_m3u8DL-RE dir : {nm3u8_dir}")
    _log_fn(f"[download] ffmpeg dir       : {ffmpeg_dir}")
    _log_fn(f"[download] mkvmerge dir     : {mkvmerge_dir}")

    # Build deduplicated PATH list — venv Scripts first, then tools
    seen = set()
    tools_dirs = []
    for d in [str(venv_scripts), str(tools_bin), nm3u8_dir,
              ffmpeg_dir, mkvmerge_dir, str(mkv_dir),
              "C:\\Program Files (x86)\\MKVToolNix"]:
        if d and d not in seen:
            seen.add(d)
            tools_dirs.append(d)

    path_prepend = ";".join(tools_dirs)
    # Display command for debugging — shows exactly what's being run
    cmd_display = " ".join(str(a) for a in resolved_command)

    script_lines = [
        # Extend PATH with all tool locations
        f"$env:PATH = '{path_prepend}' + ';' + $env:PATH",
        # Clear PyInstaller-injected Python env vars so uv uses the real Python,
        # not the frozen bundle's _MEIPASS temp dir (causes 0xC000007B otherwise)
        "Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue",
        "Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue",
        # Suppress Python SyntaxWarnings from third-party packages (e.g. tinycss)
        "$env:PYTHONWARNINGS = 'ignore'",
        "$env:PYTHONUTF8 = '1'",
        f"Set-Location {ps_quote(cwd)}",
        # Show command at top of window so user can see what's being run
        f"Write-Host 'Running: {cmd_display}' -ForegroundColor DarkGray",
        "Write-Host ''",
        # Run the command; capture exit code so we can report errors
        f"& {cmd_ps}",
        "$exit_code = $LASTEXITCODE",
        "Write-Host ''",
        # Show success or failure clearly
        "if ($exit_code -eq 0) {",
        "    Write-Host 'Download complete.' -ForegroundColor Green",
        "} else {",
        "    Write-Host 'Command exited with code ' + $exit_code -ForegroundColor Red",
        "}",
        "Write-Host 'Press any key to close...' -ForegroundColor DarkGray",
        "$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')",
    ]
    script_content = "\r\n".join(script_lines)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False,
        encoding="utf-8", prefix="twinvine_dl_"
    )
    tmp.write(script_content)
    tmp.close()
    script_path = tmp.name
    _log_fn(f"[download] Wrote helper script: {script_path}")

    # Use powershell.exe or pwsh.exe directly — NOT wt.exe.
    # wt.exe (Windows Terminal) exits immediately after spawning a tab,
    # so proc.wait() returns instantly and the queue opens the next window
    # simultaneously, defeating the sequential download logic.
    term = next((e for e in ("pwsh.exe", "powershell.exe")
                 if _shutil.which(e)), None)
    if term is None:
        _log_fn("[download] ERROR: powershell.exe not found")
        return

    outer = [term, "-ExecutionPolicy", "Bypass", "-File", script_path]

    _log_fn(f"[download] Opening terminal: {term}")
    # proc.wait() blocks until the PowerShell window closes — ensures
    # sequential downloads, one window at a time
    proc = subprocess.Popen(outer, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    proc.wait()
    _log_fn("[download] Episode complete, moving to next in queue...")



# ── Worker thread ─────────────────────────────────────────────────────────────

class FetchTracksWorker(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, uv_exe, install_dir, service, url):
        super().__init__()
        self.uv_exe      = str(uv_exe)
        self.install_dir = Path(install_dir)
        self.service     = service
        self.url         = url

    def run(self):
        import subprocess as _sp
        try:
            cmd = [self.uv_exe, "run", "--no-sync", "envied", "dl",
                   "--list", self.service, self.url]
            self.log_line.emit(f"[fetch] {' '.join(cmd)}")
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            r = _sp.run(cmd, cwd=str(self.install_dir),
                        capture_output=True, text=True, timeout=90,
                        creationflags=_sp.CREATE_NO_WINDOW,
                        encoding="utf-8", errors="replace",
                        env=env)
            self.log_line.emit(f"[fetch] return code: {r.returncode}")
            output = (r.stdout or "") + (r.stderr or "")
            for line in output.splitlines():
                if line.strip():
                    self.log_line.emit(f"[fetch] {line}")
            self.finished.emit(output)
        except Exception as e:
            self.error.emit(str(e))


def _rktn_pair_device() -> dict:
    """Login to gizmo.rakuten.tv and return session fields needed for API calls.
    Reads RKTN credentials from envied.yaml. Raises RuntimeError on failure."""
    import json as _json, urllib.request as _req, urllib.parse as _up
    from pathlib import Path as _P
    cfg = load_config()
    yaml_path = _P(cfg.get("install_dir", "")) / "packages/envied/src/envied/envied.yaml"
    username = password = ""
    try:
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("RKTN:"):
                cred = line.split(":", 1)[1].strip()
                if ":" in cred:
                    username, password = cred.split(":", 1)
                break
    except Exception:
        pass
    if not username or not password:
        raise RuntimeError(
            "Rakuten TV credentials not found in Envied Config.\n"
            "Add your Rakuten TV email and password under the RKTN entry."
        )
    device_serial = "3187ad6c-4d1c-4cbb-9c59-8396d054eb2a"
    post_data = _up.urlencode({
        "app_version": "3.22.0",
        "device_metadata[uid]": device_serial,
        "device_metadata[os]": "Android",
        "device_metadata[model]": "SM-A105FN",
        "device_metadata[year]": "2021",
        "device_metadata[trusted_uid]": "false",
        "device_metadata[brand]": "Samsung",
        "device_metadata[app_version]": "3.22.0",
        "device_serial": device_serial,
        "device_metadata[serial_number]": device_serial,
        "classification_id": "69",
        "user[username]": username,
        "user[password]": password,
    }).encode()
    req = _req.Request(
        "https://gizmo.rakuten.tv/v3/me/login_or_wuaki_link?device_identifier=android",
        data=post_data,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; SM-A105FN) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://rakuten.tv",
        },
        method="POST",
    )
    with _req.urlopen(req, timeout=20) as resp:
        res = _json.loads(resp.read().decode("utf-8"))
    if "errors" in res:
        err = res["errors"][0]
        raise RuntimeError(f"Rakuten TV login failed: {err.get('message', err)}")
    d = res["data"]
    return {
        "session_uuid":       d["user"]["session_uuid"],
        "access_token":       d["user"]["access_token"],
        "classification_id":  str(d["user"]["profile"]["classification"]["id"]),
        "market_code":        d["market"]["code"],
        "locale":             d["market"]["locale"],
        "device_identifier":  "android",
        "device_serial":      device_serial,
    }


class SearchWorker(QThread):
    """Searches a streaming service for shows matching a keyword."""
    results_ready = pyqtSignal(list)   # list of {title, url, synopsis}
    error         = pyqtSignal(str)

    def __init__(self, service_id: str, term: str):
        super().__init__()
        self._svc  = service_id
        self._term = term

    def _fetch_json(self, url: str, headers: dict | None = None,
                    post_json: dict | None = None) -> dict | list:
        import urllib.request
        import gzip as _gz
        hdrs: dict = {
            "Accept":          "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        if headers:
            hdrs.update(headers)
        body = None
        if post_json is not None:
            body = json.dumps(post_json).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            text = raw.decode("utf-8")
            return json.loads(text) if text.strip() else {}

    def run(self):
        import urllib.parse
        try:
            qt  = urllib.parse.quote(self._term)
            svc = self._svc
            if   svc == "iP":   results = self._bbc(qt)
            elif svc == "ALL4": results = self._all4(qt)
            elif svc == "ITV":  results = self._itvx(qt)
            elif svc == "MY5":  results = self._my5(qt)
            elif svc == "PLEX": results = self._plex(qt)
            elif svc == "STV":  results = self._stv(self._term)
            elif svc == "TVNZ": results = self._tvnz(qt)
            elif svc == "TPTV": results = self._tptv(qt)
            elif svc == "RTE":   results = self._rte(qt)
            elif svc == "UKTV":  results = self._uktv(qt)
            elif svc == "PLUTO": results = self._pluto(self._term)
            elif svc == "TUBI":  results = self._tubi_search(self._term)
            elif svc == "NINE":  results = self._nine_search(self._term)
            elif svc == "NBC":   results = self._nbc_search(self._term)
            elif svc == "RKTN":  results = self._rktn_search(self._term)
            elif svc == "VM":    results = self._vm_search(self._term)
            elif svc == "ROKU":  results = self._roku_search(self._term)
            elif svc == "CBS":   results = self._cbs_search(self._term)
            elif svc == "CWTV":  results = self._cwtv_search(self._term)
            elif svc == "PBS":   results = self._pbs_search(self._term)
            elif svc == "CRAV":  results = self._crav_search(self._term)
            elif svc == "CBC":   results = self._cbc_search(self._term)
            elif svc == "ThreeNow": results = self._threenow_search(self._term)
            elif svc == "AUBC":    results = self._aubc_search(self._term)
            elif svc == "SEVEN":   results = self._seven_search(self._term)
            elif svc == "TEN":     results = self._ten_search(self._term)
            elif svc == "SBS":     results = self._sbs_search(self._term)
            else:
                self.error.emit(f"Search not available for {svc}.")
                return
            if not results:
                self.error.emit("No results found.")
            else:
                self.results_ready.emit(results)
        except Exception as exc:
            self.error.emit(f"Search failed: {exc}")

    def _bbc(self, qt: str) -> list:
        import gzip as _gz
        data = self._fetch_json(
            f"https://ibl.api.bbc.co.uk/ibl/v1/new-search?q={qt}&rights=web"
        )
        out = []
        for item in data.get("new_search", {}).get("results", []):
            pid = item.get("id", "")
            if not pid:
                continue
            out.append({
                "title":    item.get("title", pid),
                "synopsis": item.get("synopsis", ""),
                "url":      f"https://www.bbc.co.uk/iplayer/brand/{pid}",
            })
        return out

    def _all4(self, qt: str) -> list:
        data = self._fetch_json(
            f"https://all4nav.channel4.com/v1/api/search?q={qt}&limit=100"
        )
        out, seen = [], set()
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            brand = item.get("brand", {})
            title = brand.get("title", "")
            href  = brand.get("href", "")
            if not href or title in seen:
                continue
            seen.add(title)
            url = f"https://www.channel4.com{href}" if not href.startswith("http") else href
            out.append({"title": title, "synopsis": brand.get("description", ""), "url": url})
        return out

    def _itvx(self, qt: str) -> list:
        url = (
            f"https://textsearch.prd.oasvc.itv.com/search?broadcaster=itv"
            f"&featureSet=clearkey,outband-webvtt,hls,aes,playready,widevine,"
            f"fairplay,bbts,progressive,hd,rtmpe&platform=dotcom&pretx=true"
            f"&query={qt}&size=24"
        )
        data = self._fetch_json(url, headers={
            "Host":    "textsearch.prd.oasvc.itv.com",
            "Origin":  "https://www.itv.com",
            "Referer": "https://www.itv.com/",
        })
        out = []
        for item in data.get("results", []):
            d = item.get("data", {})
            if d.get("tier") != "FREE":
                continue
            title = (d.get("programmeTitle") or d.get("filmTitle")
                     or d.get("specialTitle") or "")
            if not title:
                continue
            # Use officialFormat (e.g. "10/5961/0001") and replace "/" with "a"
            # officialFormat e.g. "10/5961/0001" — replace "/" with "a" for the episode ID
            official = d.get("legacyId", {}).get("officialFormat", "")
            _id = official.replace("/", "a")
            # Slug: simple space→dash, no case-changing of special chars
            slug = title.replace(" ", "-")
            out.append({
                "title":    title,
                "synopsis": d.get("synopsis", ""),
                "url":      f"https://www.itv.com/watch/{slug}/{_id}",
            })
        return out

    def _my5(self, qt: str) -> list:
        data = self._fetch_json(
            f"https://corona.channel5.com/shows/search.json"
            f"?platform=my5desktop&friendly=1&query={qt}"
        )
        out = []
        for item in (data.get("shows") or []):
            if not isinstance(item, dict):
                continue
            f_name = item.get("f_name", "")
            if not f_name:
                continue
            out.append({
                "title":    item.get("title", f_name),
                "synopsis": item.get("s_desc", ""),
                "url":      f"https://corona.channel5.com/shows/{f_name}/seasons.json?platform=my5desktop&friendly=1",
            })
        return out

    def _plex(self, qt: str) -> list:
        import uuid as _uuid
        data = self._fetch_json(
            f"https://discover.provider.plex.tv/library/search/"
            f"?searchProviders=discover,plexAVOD,plexFAST"
            f"&includeGroups=1&searchTypes=all%2Cmovies%2Ctv"
            f"&includeMetadata=1&filterPeople=1&limit=10&query={qt}",
            headers={
                "Accept": "application/json",
                "X-Plex-Product": "Plex Mediaverse",
                "X-Plex-Version": "1.0",
                "X-Plex-Client-Identifier": str(_uuid.uuid4()),
            },
        )
        import jmespath as _jmp
        res = _jmp.search(
            "MediaContainer.SearchResults[?id=='plex'] | [0].SearchResult[].{"
            "slug: Metadata.slug, title: Metadata.title, type: Metadata.type}",
            data,
        ) or []
        out = []
        for item in res:
            if not isinstance(item, dict):
                continue
            slug  = item.get("slug", "")
            title = item.get("title", "")
            typ   = item.get("type", "show")
            if not title or not slug:
                continue
            out.append({
                "title":    title,
                "synopsis": typ,
                "url":      f"https://watch.plex.tv/{typ}/{slug}",
            })
        return out

    def _stv(self, term: str) -> list:
        # POST directly to swiftype — no OPTIONS preflight needed
        import json as _json
        payload = _json.dumps({
            "engine_key": "S1jgssBHdk8ZtMWngK_y",
            "per_page": 100, "page": 1,
            "fetch_fields": {"page": ["title", "body", "resultDescriptionTx", "url"]},
            "search_fields": {"page": ["title^3", "body", "category", "sections"]},
            "q": term, "spelling": "strict",
        }).encode()
        import urllib.request as _ur
        req = _ur.Request(
            "https://search-api.swiftype.com/api/v1/public/engines/search.json",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://player.stv.tv",
                "Referer": "https://player.stv.tv/",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            },
            method="POST",
        )
        with _ur.urlopen(req, timeout=20) as resp:
            parsed_data = _json.loads(resp.read().decode())
        out = []
        for item in (parsed_data.get("records", {}).get("page") or []):
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            url   = item.get("url", "")
            if not title or not url:
                continue
            out.append({
                "title":    title,
                "synopsis": item.get("resultDescriptionTx", ""),
                "url":      url,
            })
        return out

    def _tvnz(self, qt: str) -> list:
        import urllib.parse as _up
        params = _up.urlencode({
            "mode": "detail",
            "st": "published",
            "term": qt,
            "pageNumber": "1",
            "pageSize": "50",
            "reg": "nz",
            "dt": "androidtv",
            "client": "tvnz-tvnz-androidtv",
            "pf": "Regular",
            "allowpg": "true",
        })
        data = self._fetch_json(
            f"https://search-cdn.cms-api.tvnz.co.nz/content/search?{params}",
            {
                "x-device-type": "androidtv",
                "x-app-store-type": "androidtv",
                "x-client-id": "tvnz-tvnz-androidtv",
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; Android TV Build/RTMA.250416.082)",
            },
        )
        if data.get("header", {}).get("message", "").lower() != "success":
            return []
        out = []
        for item in (data.get("data") or []):
            if not isinstance(item, dict):
                continue
            cty   = item.get("cty", "")
            nu    = item.get("nu", "")
            title = (item.get("lon") or [{"n": ""}])[0].get("n", "")
            synopsis = (item.get("losd") or [{"n": ""}])[0].get("n", "")
            if not title or not nu or not cty:
                continue
            url = f"https://tvnz.co.nz/{cty}/{nu}"
            out.append({
                "title":    title,
                "synopsis": synopsis,
                "url":      url,
            })
        return out

    def _tptv(self, qt: str) -> list:
        # suggestedtv.com does TLS fingerprinting and rejects requests/urllib.
        # httpx (httpcore) passes; it lives in the TwinVine venv so we inject
        # the venv site-packages into sys.path before importing.
        import sys as _sys
        cfg = load_config()
        _venv_sp = Path(cfg.get("install_dir", "")) / ".venv" / "Lib" / "site-packages"
        if _venv_sp.exists() and str(_venv_sp) not in _sys.path:
            _sys.path.insert(0, str(_venv_sp))
        import httpx as _httpx

        api_key = "zq5pyPd0RTbNg3Fyj52PrkKL9c2Af38HHh4itgZTKDaCzjAyhd"
        base_hdrs = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.5",
            "api-key": api_key,
            "Referer": "https://tptvencore.co.uk/",
            "tenant": "encore",
            "Content-Type": "application/json",
            "Origin": "https://tptvencore.co.uk",
            "DNT": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "Priority": "u=0",
        }
        with _httpx.Client(timeout=20, follow_redirects=True) as client:
            # Try search directly with api-key only (session endpoint may have changed)
            direct_hdrs = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "api-key": api_key,
                "tenant": "encore",
                "Origin": "https://tptvencore.co.uk",
                "Referer": "https://tptvencore.co.uk/",
            }
            r_direct = client.get(
                f"https://tptvencore.co.uk/api/core/search?q={qt}&page=1&pageSize=40&locale=en",
                headers=direct_hdrs,
            )
            if r_direct.status_code == 200:
                data = r_direct.json()
            else:
                # Fall back to session-based flow
                r = client.post(
                    "https://prod.suggestedtv.com/api/client/v1/session",
                    content=b"{}",
                    headers=base_hdrs,
                )
                if r.status_code != 200:
                    raise ConnectionError(
                        f"TPTV session endpoint returned HTTP {r.status_code}. "
                        "The TPTV API may have changed — search is currently unavailable."
                    )
                session_id = r.json()["id"]
                search_hdrs = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                    "Connection": "keep-alive",
                    "Origin": "https://tptvencore.co.uk",
                    "Referer": "https://tptvencore.co.uk/",
                    "Access-Control-Request-Headers": "session,tenant",
                    "Access-Control-Request-Method": "GET",
                    "session": session_id,
                }
                r2 = client.get(
                    f"https://tptvencore.co.uk/api/core/search?q={qt}&page=1&pageSize=40&locale=en",
                    headers=search_hdrs,
                )
                if r2.status_code != 200:
                    raise ConnectionError(f"TPTV search failed: HTTP {r2.status_code}")
                data = r2.json()
        out = []
        for item in (data.get("data") or []):
            if not isinstance(item, dict):
                continue
            title    = item.get("title", "")
            playback = (item.get("video") or {}).get("playback", "")
            synopsis = (item.get("description") or "").replace("\n", " ")
            if not title or not playback:
                continue
            playback = playback.replace("api/core/play", "playback")
            vid_id   = playback.rstrip("/").split("/")[-1].replace("?locale=en", "")
            url      = f"https://tptvencore.co.uk/playback/item/{vid_id}"
            if len(synopsis) > 300:
                synopsis = synopsis[:300] + "..."
            out.append({"title": title, "synopsis": synopsis, "url": url})
        return out

    def _rte(self, qt: str) -> list:
        import urllib.parse as _up
        params = _up.urlencode({
            "byProgramType": "Series|Movie",
            "q": f"title:({qt})",
            "range": "0-40",
            "schema": "2.15",
            "sort": "rte$rank|desc",
            "gzip": "true",
            "omitInvalidFields": "true",
        })
        data = self._fetch_json(
            f"https://feed.entertainment.tv.theplatform.eu/f/1uC-gC/rte-prd-prd-search?{params}"
        )
        import re as _re
        out = []
        for result in (data.get("entries") or []):
            if not isinstance(result, dict):
                continue
            is_series = result.get("plprogram$programType", "").lower() == "series"
            _id   = result.get("guid") if is_series else (result.get("id") or "").split("/")[-1]
            _title = result.get("title") if is_series else result.get("plprogram$longTitle", "")
            _type = result.get("plprogram$programType", "Series")
            if not _title or not _id:
                continue
            title_slug = _re.sub(r"^-|-$", "", _re.sub(r"\W+", "-", _title.lower()))
            out.append({
                "title":    _title,
                "synopsis": result.get("plprogram$shortDescription", ""),
                "url":      f"https://www.rte.ie/player/{_type}/{title_slug}/{_id}",
                "_rte_guid": _id,
                "_rte_type": _type,
            })
        return out

    def _uktv(self, qt: str) -> list:
        data = self._fetch_json(
            f"https://vschedules.uktv.co.uk/vod/search/?q={qt}"
        )
        # UKTV search API returns a list directly (not a dict with "results")
        items = data if isinstance(data, list) else (data.get("results") or [])
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("name") or item.get("title", "")
            slug  = item.get("slug", "")
            if not title or not slug or item.get("type") == "COLLECTION":
                continue
            out.append({
                "title":    title,
                "synopsis": item.get("synopsis", ""),
                # Pass brand API URL directly so BrowseWorker can fetch it without slug extraction
                "url":      f"https://vschedules.uktv.co.uk/vod/brand/?slug={slug}",
            })
        return out

    def _pluto(self, term: str) -> list:
        import uuid as _uuid, urllib.parse as _up
        boot_qs = _up.urlencode({
            "appName": "web", "appVersion": "na",
            "clientID": str(_uuid.uuid1()), "deviceDNT": 0,
            "deviceId": "unknown", "clientModelNumber": "na",
            "serverSideAds": "false", "deviceMake": "unknown",
            "deviceModel": "web", "deviceType": "web",
            "deviceVersion": "unknown", "sid": str(_uuid.uuid1()),
            "drmCapabilities": "widevine:L3",
        })
        boot = self._fetch_json(f"https://boot.pluto.tv/v4/start?{boot_qs}")
        token  = boot.get("sessionToken", "")
        region = boot.get("session", {}).get("activeRegion", "").lower()
        qt = _up.quote(term)
        data = self._fetch_json(
            f"https://service-media-search.clusters.pluto.tv/v1/search?q={qt}&limit=50",
            headers={"Authorization": f"Bearer {token}"},
        )
        out = []
        for result in data.get("data", []):
            if result.get("type") in ("timeline", "channel"):
                continue
            content_id = result.get("id", "")
            kind = "movies" if result.get("type") == "movie" else "series"
            base = f"https://pluto.tv/{region}/on-demand" if region else "https://pluto.tv/on-demand"
            url  = f"{base}/{kind}/{content_id}/details"
            out.append({
                "title":    result.get("name", content_id),
                "synopsis": result.get("synopsis", ""),
                "url":      url,
                "_kind":    kind,
            })
        return out

    @staticmethod
    def _tubi_cookies() -> tuple[str, str | None]:
        """
        Read tubitv.com cookies from EnvyCore/Cookies/Tubi.txt.
        Returns (cookie_header_string, at_token_or_None).
        Raises RuntimeError with a user-friendly message if unusable.
        """
        import base64 as _b64, json as _json, time as _time
        from pathlib import Path as _P
        cfg = load_config()
        cookie_file = _P(cfg.get("install_dir", "")) / "Cookies" / "Tubi.txt"
        if not cookie_file.exists():
            raise RuntimeError(
                "Tubi search requires a cookie file.\n"
                "Log in to tubitv.com in your browser, export cookies to a file named "
                "Tubi.txt and place it in EnvyCore/Cookies/."
            )
        pairs: list[str] = []
        at_token: str | None = None
        try:
            for line in cookie_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, _, _, _, name, value = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
                if "tubitv.com" not in domain:
                    continue
                pairs.append(f"{name}={value}")
                if name == "at":
                    at_token = value
        except Exception as exc:
            raise RuntimeError(f"Could not read Tubi.txt: {exc}") from exc
        if not pairs:
            raise RuntimeError(
                "Tubi.txt does not contain any tubitv.com cookies.\n"
                "Log in to tubitv.com in your browser and re-export the cookies."
            )
        # Check if the `at` JWT has expired
        if at_token:
            try:
                payload_b64 = at_token.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                exp = _json.loads(_b64.urlsafe_b64decode(payload_b64)).get("exp", 0)
                if exp and exp < _time.time():
                    raise RuntimeError(
                        "Your Tubi cookie (at= token) has expired.\n"
                        "Log in to tubitv.com in your browser and re-export the cookies, "
                        "then replace EnvyCore/Cookies/Tubi.txt with the new file."
                    )
            except RuntimeError:
                raise
            except Exception:
                pass  # If JWT decode fails, try anyway
        return "; ".join(pairs), at_token

    def _tubi_search(self, term: str) -> list:
        import urllib.parse as _up
        cookie_hdr, at_token = self._tubi_cookies()
        params = _up.urlencode({
            "search":            term,
            "include_linear":    "false",
            "include_channels":  "false",
            "is_kids_mode":      "false",
        })
        hdrs = {"Cookie": cookie_hdr}
        if at_token:
            hdrs["Authorization"] = f"Bearer {at_token}"
        data = self._fetch_json(
            f"https://search.production-public.tubi.io/api/v2/search?{params}",
            headers=hdrs,
        )
        contents = data.get("contents") or {}
        # Use containers ordering — this is the relevance-ranked display order
        containers = data.get("containers") or []
        if isinstance(containers, dict):
            containers = [containers]
        ordered_ids = []
        for container in containers:
            ordered_ids.extend(container.get("children") or [])
        # Fall back to contents insertion order if containers is empty
        if not ordered_ids:
            ordered_ids = list(contents.keys())
        out = []
        seen: set = set()
        for cid in ordered_ids:
            item = contents.get(cid)
            if item is None:
                continue
            content_id = item.get("id", "")
            if content_id in seen:
                continue
            seen.add(content_id)
            kind_code = item.get("type", "s")
            url = (f"https://tubitv.com/movies/{content_id}"
                   if kind_code == "v" else
                   f"https://tubitv.com/series/{content_id}")
            out.append({
                "title":    item.get("title", content_id),
                "synopsis": item.get("description", ""),
                "url":      url,
            })
        return out

    def _nine_search(self, term: str) -> list:
        import urllib.parse as _up
        from urllib.parse import urljoin as _urljoin
        params = _up.urlencode({"q": term.strip(), "device": "web"})
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.9now.com.au",
            "Referer": "https://www.9now.com.au/",
        }
        data = self._fetch_json(
            f"https://tv-api.9now.com.au/v2/pages/search?{params}",
            headers=headers,
        )
        out = []
        seen: set = set()
        for group in data.get("results", []):
            if group.get("title") != "Search results":
                continue
            for result in group.get("items", []):
                link = result.get("link") or {}
                web_url = link.get("webUrl")
                if result.get("type") != "tv-series" or not web_url:
                    continue
                url = _urljoin("https://www.9now.com.au", web_url)
                if url in seen:
                    continue
                seen.add(url)
                out.append({
                    "title":    result.get("name") or result.get("displayName", ""),
                    "synopsis": result.get("description", ""),
                    "url":      url,
                })
        return out

    def _nbc_search(self, term: str) -> list:
        import json as _json, urllib.parse as _up
        algolia_url  = "https://3nkvntt7f3-dsn.algolia.net/1/indexes/*/queries"
        app_id       = "3NKVNTT7F3"
        api_key      = "c2df90d0ff616a2726139c671d6e6e8e"
        index        = "prod_multi-brand-unified-web"
        facet_filters = _json.dumps([["algoliaProperties.entityType:series",
                                       "algoliaProperties.entityType:episodes"]])
        params = _up.urlencode({
            "query":        term,
            "facetFilters": facet_filters,
            "page":         0,
            "hitsPerPage":  20,
        })
        data = self._fetch_json(
            algolia_url,
            headers={
                "User-Agent":              "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Origin":                  "https://www.nbc.com",
                "Referer":                 "https://www.nbc.com/",
                "x-algolia-api-key":       api_key,
                "x-algolia-application-id": app_id,
            },
            post_json={"requests": [{"indexName": index, "params": params}]},
        )
        out = []
        for hit in (data.get("results") or [{}])[0].get("hits") or []:
            entity_type = (hit.get("algoliaProperties") or {}).get("entityType")
            if entity_type == "series":
                series_data = hit.get("series") or {}
                slug = series_data.get("urlAlias") or series_data.get("seriesName")
                if not slug:
                    continue
                out.append({
                    "title":    series_data.get("shortTitle") or slug,
                    "synopsis": series_data.get("shortDescription", ""),
                    "url":      f"https://www.nbc.com/{slug}",
                })
            elif entity_type == "episodes":
                ep_data    = hit.get("episegment") or {}
                video      = hit.get("video") or {}
                series_data = hit.get("series") or {}
                permalink  = (video.get("permalink") or "").replace("http://", "https://")
                if not permalink:
                    continue
                out.append({
                    "title":    ep_data.get("title") or "(untitled)",
                    "synopsis": ep_data.get("shortDescription", ""),
                    "url":      permalink,
                })
        return out

    def _rktn_search(self, term: str) -> list:
        import urllib.parse as _up
        base = {
            "classification_id":      "18",
            "device_identifier":      "web",
            "locale":                 "en",
            "market_code":            "uk",
            "page":                   "1",
            "per_page":               "36",
            "personalization_consent": "true",
            "query":                  term,
            "search_engine":          "external",
        }
        out  = []
        seen = set()
        for content_type in ("movies", "tv_shows"):
            try:
                qs    = _up.urlencode(base)
                data  = self._fetch_json(f"https://gizmo.rakuten.tv/v3/{content_type}?{qs}")
                items = data.get("data") or []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    permalink = item.get("id") or item.get("permalink", "")
                    if not permalink:
                        continue
                    url_key = f"{content_type}/{permalink}"
                    if url_key in seen:
                        continue
                    seen.add(url_key)
                    out.append({
                        "title":    item.get("title", permalink),
                        "synopsis": item.get("short_plot") or item.get("plot", ""),
                        "url":      f"https://www.rakuten.tv/uk/{content_type}/{permalink}",
                    })
            except Exception:
                continue
        return out


    def _roku_search(self, term: str) -> list:
        import urllib.request as _ur, json as _json, re as _re, http.cookiejar as _cj
        HDRS = {
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          "https://therokuchannel.roku.com",
            "Referer":         "https://therokuchannel.roku.com/",
        }
        jar    = _cj.CookieJar()
        opener = _ur.build_opener(_ur.HTTPCookieProcessor(jar))

        def _get(url, extra=None):
            req = _ur.Request(url, headers={**HDRS, **(extra or {})})
            with opener.open(req, timeout=20) as r:
                raw = r.read()
                if not raw.strip():
                    raise RuntimeError("Roku is geofenced to the US — a US VPN is required")
                return _json.loads(raw.decode("utf-8"))

        def _post(url, payload, extra=None):
            hdrs = {**HDRS, "Content-Type": "application/json", **(extra or {})}
            req  = _ur.Request(url, data=_json.dumps(payload).encode(), headers=hdrs, method="POST")
            with opener.open(req, timeout=20) as r:
                return _json.loads(r.read().decode("utf-8"))

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "title"

        # Visit homepage first to establish session cookies (required for valid CSRF)
        opener.open(_ur.Request("https://therokuchannel.roku.com/", headers=HDRS))

        token   = _get("https://therokuchannel.roku.com/api/v1/csrf").get("csrf", "")
        results = _post(
            "https://therokuchannel.roku.com/api/v1/search",
            {"query": term},
            {"csrf-token": token},
        )
        out = []
        for item in results.get("view") or []:
            c     = item.get("content") or {}
            ctype = c.get("type", "")
            if ctype in ("zone", "provider"):
                continue
            cid = (c.get("meta") or {}).get("id", "")
            if not cid:
                continue
            title    = c.get("title") or cid
            desc_obj = c.get("descriptions") or {}
            synopsis = (desc_obj.get("250") or {}).get("text") or ""
            # Encode type in URL so _on_show_selected can route without an extra API call
            type_seg = "movie" if ctype in ("movie", "tvspecial") else "series"
            out.append({
                "title":    title,
                "synopsis": synopsis,
                "url":      f"https://therokuchannel.roku.com/details/{cid}/{type_seg}/{_slugify(title)}",
            })
        return out

    def _cbs_search(self, term: str) -> list:
        import urllib.request as _ur, json as _json, urllib.parse as _up
        BASE  = "https://cbsdigital.cbs.com"
        TOKEN = "ABBsaBMagMmYLUc9iXB0lXEKsUQ0/MwRn6z3Tg0KKQaH7Q6QGqJcABwlBP4XiMR1b0Q="
        HDRS  = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
            "Accept":     "application/json",
        }
        qs   = _up.urlencode({"at": TOKEN, "term": term, "termCount": "50", "showCanVids": "true"})
        req  = _ur.Request(f"{BASE}/apps-api/v3.1/androidphone/contentsearch/search.json?{qs}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8"))
        out = []
        for result in data.get("terms") or []:
            path  = result.get("path") or ""
            title = result.get("title") or path
            label = result.get("term_type") or ""
            if not path or label.lower() in ("cast", "topic", "genre"):
                continue
            url = path if path.startswith("http") else f"https://www.cbs.com/{path.lstrip('/')}"
            out.append({"title": title, "synopsis": label, "url": url})
        return out

    def _cwtv_search(self, term: str) -> list:
        import urllib.request as _ur, json as _json, urllib.parse as _up
        HDRS = {"User-Agent": "Mozilla/5.0 (Linux; Android 11; Smart TV Build/AR2101; wv)",
                "Accept": "application/json"}
        qs  = _up.urlencode({"q": term, "format": "json2", "service": "t", "cwuid": "8195356001251527455"})
        req = _ur.Request(f"https://www.cwtv.com/search/?{qs}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8"))
        out = []
        for item in data.get("items") or []:
            kind = item.get("type", "")
            if kind not in ("shows", "series", "movies"):
                continue
            slug = item.get("show_slug") or ""
            if not slug:
                continue
            video_type = "movies" if kind == "movies" else "shows"
            out.append({
                "title":    item.get("title") or slug,
                "synopsis": item.get("description_long") or "",
                "url":      f"https://www.cwtv.com/{video_type}/{slug}",
            })
        return out

    def _pbs_search(self, term: str) -> list:
        return self._pbs_graphql(title=term, genre=None, first=20, ordering="POPULAR")

    @staticmethod
    def _pbs_graphql(title="", genre=None, first=50, ordering="POPULAR", paginate=False) -> list:
        import urllib.request as _ur, json as _json
        HDRS = {
            "User-Agent":            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Content-Type":          "application/json",
            "Accept":                "*/*",
            "Origin":                "https://www.pbs.org",
            "Referer":               "https://www.pbs.org/",
            "x-pbs-platform":        "pbsorg",
            "x-pbs-platform-version":"1.0",
            "x-pbs-station-id":      "7387eb2c-e0ce-4069-82d9-08865df87edf",
        }
        GQL = (
            "query SearchShowsQuery($first:Int!,$ordering:AllShowsOrdering!,$title:String,"
            "$genre:Genre,$source:AllShowsSource,$after:String){"
            "searchShows(first:$first ordering:$ordering title:$title genre:$genre source:$source after:$after){"
            "edges{node{description:descriptionLong genre title slug}}"
            "pageInfo{hasNextPage endCursor}}}"
        )

        def _fetch(after=None):
            payload = _json.dumps({
                "query":     GQL,
                "variables": {"first": first, "ordering": ordering, "title": title or "",
                              "genre": genre, "source": None, "after": after},
            }).encode("utf-8")
            req = _ur.Request("https://content.services.pbs.org/graphql/", data=payload, headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                data = _json.loads(r.read().decode("utf-8"))
            return (data.get("data") or {}).get("searchShows") or {}

        out    = []
        seen   = set()
        cursor = None
        while True:
            result = _fetch(cursor)
            for edge in result.get("edges") or []:
                node   = edge.get("node") or {}
                slug   = node.get("slug") or ""
                title_ = node.get("title") or slug
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                out.append({
                    "title":    title_,
                    "synopsis": node.get("description") or "",
                    "url":      f"https://www.pbs.org/show/{slug}/",
                })
            page_info = result.get("pageInfo") or {}
            if not paginate or not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
        return out

    def _crav_search(self, term: str) -> list:
        import urllib.request as _ur, json as _json
        token = _crav_get_graphql_token()
        if not token:
            raise RuntimeError("No CRAV credentials found. Add username/password to EnvyCore/Cookies/CRAV.txt (username on line 1, password on line 2).")
        HDRS = {
            "User-Agent":    "Dalvik/2.1.0 (Linux; U; Android 11; SHIELD Android TV Build/RQ1A.210105.003)",
            "Content-Type":  "application/json",
            "Accept":        "*/*",
            "authorization": f"Bearer {token}",
        }
        GQL = (
            "query GetSearch($sessionContext:SessionContext!,$searchQuery:String!,$pageNumber:Int!,$pageSize:Int!,$collection:SearchCollectionType!){"
            "search(sessionContext:$sessionContext searchRequest:{searchQuery:$searchQuery pageNumber:$pageNumber pageSize:$pageSize collection:$collection}){"
            "mediaResults{...on MediaMetadata{id title path mediaType shortDescription}}}}"
        )
        payload = _json.dumps({
            "query": GQL,
            "variables": {
                "searchQuery":   term,
                "pageSize":      20,
                "pageNumber":    1,
                "collection":    "MEDIAS",
                "sessionContext": {"userMaturity": "ADULT", "userLanguage": "EN"},
            },
        }).encode()
        req = _ur.Request("https://rte-api.bellmedia.ca/graphql", data=payload, headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        out = []
        for node in (data.get("data") or {}).get("search", {}).get("mediaResults") or []:
            path  = node.get("path") or ""
            title = node.get("title") or ""
            if not path or not title:
                continue
            out.append({
                "title":    title,
                "synopsis": node.get("shortDescription") or "",
                "url":      f"https://www.crave.ca/{path.lstrip('/')}",
            })
        return out

    def _cbc_search(self, term: str) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        BASE = "https://services.radio-canada.ca"
        qs = _up.urlencode({"device": "web", "pageNumber": 1, "pageSize": 20, "term": term})
        req = _ur.Request(f"{BASE}/ott/catalog/v1/gem/search?{qs}",
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        out = []
        for item in (data.get("result") or []):
            url   = item.get("url") or ""
            title = item.get("title") or ""
            if not url or not title:
                continue
            if not url.startswith("http"):
                url = f"https://gem.cbc.ca/{url.lstrip('/')}"
            out.append({"title": title, "synopsis": item.get("description") or "", "url": url})
        return out

    def _threenow_search(self, term: str) -> list:
        import urllib.request as _ur, json as _json
        req = _ur.Request(
            "https://now-api.fullscreen.nz/v5/shows",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        term_l = term.lower()
        out = []
        for show in (data.get("shows") or []):
            title = show.get("name") or ""
            if term_l not in title.lower():
                continue
            show_id = show.get("showId") or ""
            if not show_id:
                continue
            out.append({
                "title":    title,
                "synopsis": "",
                "url":      f"https://www.threenow.co.nz/shows/{show_id}",
            })
        return out

    def _aubc_search(self, term: str) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        url = (
            "https://y63q32nvdl-1.algolianet.com/1/indexes/*/queries"
            "?x-algolia-api-key=bcdf11ba901b780dc3c0a3ca677fbefc"
            "&x-algolia-application-id=Y63Q32NVDL"
        )
        payload = _json.dumps({
            "requests": [{
                "indexName": "ABC_production_iview_web",
                "params": f"query={_up.quote(term)}&tagFilters=",
            }]
        }).encode()
        req = _ur.Request(url, data=payload, method="POST",
                          headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        out = []
        for hit in (data.get("results") or [{}])[0].get("hits") or []:
            if hit.get("docType") != "Program":
                continue
            slug = hit.get("slug") or ""
            title = hit.get("title") or ""
            if not slug or not title:
                continue
            out.append({
                "title":    title,
                "synopsis": hit.get("synopsis") or "",
                "url":      f"https://iview.abc.net.au/show/{slug}",
            })
        return out

    def _seven_search(self, term: str) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "x-swm-apikey": "kGcrNnuPClrkynfnKwG8IA/NhVG6ut5nPEdWF2jscvE="}
        # Get market_id
        mreq = _ur.Request("https://market-cdn.swm.digital/v1/market/ip?apikey=web", headers=HDRS)
        try:
            with _ur.urlopen(mreq, timeout=8) as r:
                market_id = _json.loads(r.read().decode()).get("_id", 4)
        except Exception:
            market_id = 4
        qs = _up.urlencode({
            "searchTerm": term, "market-id": market_id,
            "api-version": "4.4", "platform-id": "androidtv", "platform-version": "5.25.0.0",
        })
        req = _ur.Request(f"https://searchapi.swm.digital/3.0/api/Search?{qs}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            results = _json.loads(r.read().decode())
        out = []
        for item in (results if isinstance(results, list) else []):
            title = (item.get("image") or {}).get("altTag") or ""
            slug  = (item.get("contentLink") or {}).get("url") or ""
            if not title or not slug:
                continue
            out.append({
                "title":    title,
                "synopsis": item.get("description") or "",
                "url":      f"https://7plus.com.au{slug}",
            })
        return out

    @staticmethod
    def _ten_brace_extract(html: str, marker: str) -> dict | None:
        """Find `marker` in html, then brace-balance extract the JSON object that follows."""
        import json as _json
        idx = html.find(marker)
        if idx < 0:
            return None
        try:
            json_start = html.index('{', idx)
        except ValueError:
            return None
        depth, json_end = 0, json_start
        for i, ch in enumerate(html[json_start:], json_start):
            if ch == '{':   depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        try:
            return _json.loads(html[json_start:json_end])
        except Exception:
            return None

    @staticmethod
    def _ten_shows_from_data(data: dict) -> list:
        """Pull show list from either showsPageData or searchLandingPageData."""
        out = []
        # showsPageData → .shows[].{name, url}
        # searchLandingPageData → .results[].{name, url}
        items = data.get("shows") or data.get("results") or []
        for show in items:
            name = show.get("name") or show.get("title") or ""
            url  = show.get("url") or ""
            if name and url:
                out.append({"title": name,
                            "synopsis": show.get("abstractShowDescription") or "",
                            "url": url})
        return out

    def _ten_search(self, term: str) -> list:
        import urllib.request as _ur, urllib.parse as _up
        HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*"}
        req = _ur.Request("https://10.com.au/search?" + _up.urlencode({"query": term}), headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
        data = SearchWorker._ten_brace_extract(html, "searchLandingPageData = ")
        if not data:
            return []
        out = []
        for item in (data.get("results") or []):
            if item.get("contentType") != "shows":
                continue
            title = item.get("headline") or ""
            link = item.get("link") or ""
            if not title or not link:
                continue
            out.append({"title": title,
                        "synopsis": "",
                        "url": f"https://10.com.au{link}"})
        return out

    def _sbs_search(self, term: str) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json", "Origin": "https://www.sbs.com.au"}
        req = _ur.Request("https://content-search.pr.sbsod.com/catalogue?" + _up.urlencode({"q": term}), headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        out = []
        for item in (data.get("items") or []):
            if item.get("entityType") != "TV_SERIES":
                continue
            slug = item.get("slug") or ""
            title = item.get("title") or ""
            if not slug or not title:
                continue
            out.append({"title": title,
                        "synopsis": item.get("description") or "",
                        "url": f"https://www.sbs.com.au/ondemand/tv-series/{slug}"})
        return out

    def _vm_search(self, term: str) -> list:
        import urllib.parse as _up, re as _re
        qs = _up.urlencode({
            "key":      "821254297041614280861178657602",
            "cc":       "IE",
            "lang":     "en",
            "platform": "chrome",
            "q":        term,
        })
        data = self._fetch_json(
            f"https://v6-metadata-cf.simplestreamcdn.com/api/search?{qs}",
            {"Origin": "https://play.virginmediatelevision.ie",
             "Referer": "https://play.virginmediatelevision.ie/"},
        )
        out  = []
        seen = set()

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "title"

        for section in (data.get("response") or {}).get("sections") or []:
            for item in section.get("tiles") or []:
                if not isinstance(item, dict):
                    continue
                series_id = item.get("series_id") or (item.get("id") if item.get("type") not in ("vod", "replay", "episode") else None)
                video_id  = item.get("uvid") or item.get("video_id") or item.get("content_id") or item.get("asset_id")
                title     = item.get("title") or item.get("name") or ""
                synopsis  = item.get("synopsis") or item.get("description") or ""

                if series_id and str(series_id) not in seen:
                    seen.add(str(series_id))
                    slug = _slugify(title)
                    out.append({
                        "title":    title,
                        "synopsis": synopsis,
                        "url":      f"https://play.virginmediatelevision.ie/shows/{series_id}/{slug}",
                    })
                elif video_id and str(video_id) not in seen:
                    seen.add(str(video_id))
                    slug = _slugify(title)
                    out.append({
                        "title":    title,
                        "synopsis": synopsis,
                        "url":      f"https://play.virginmediatelevision.ie/watch/vod/{video_id}/{slug}",
                    })
        return out


class CategoryWorker(QThread):
    """Fetches the list of browse categories for a service."""
    done  = pyqtSignal(list)   # list of {"name": str, "id": str}
    error = pyqtSignal(str)

    _API_KEY = "D2FgtcTxGqqIgLsfBWTJdrQh2tVdeaAp"

    def __init__(self, service_id: str):
        super().__init__()
        self._svc = service_id

    def _fetch_json(self, url: str, headers: dict | None = None) -> dict | list:
        import urllib.request, gzip as _gz, json as _json
        hdrs = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            text = raw.decode("utf-8")
            return _json.loads(text) if text.strip() else {}

    def run(self):
        try:
            svc = self._svc
            if svc == "iP":
                cats = self._bbc()
            elif svc == "ALL4":
                cats = self._all4()
            elif svc == "ITV":
                cats = self._itvx()
            elif svc == "MY5":
                cats = self._my5()
            elif svc == "UKTV":
                cats = self._uktv()
            elif svc == "PLEX":
                cats = self._plex()
            elif svc == "STV":
                cats = self._stv()
            elif svc == "TVNZ":
                cats = self._tvnz()
            elif svc == "TPTV":
                cats = self._tptv()
            elif svc == "TUBI":
                cats = self._tubi()
            elif svc == "PLUTO":
                cats = self._pluto()
            elif svc == "NINE":
                cats = self._nine()
            elif svc == "NBC":
                cats = self._nbc()
            elif svc == "RKTN":
                cats = self._rktn()
            elif svc == "RKTN:movies":
                cats = self._rktn_genres("movies")
            elif svc == "RKTN:tv_shows":
                cats = self._rktn_genres("tv_shows")
            elif svc == "CRAV:shows":
                cats = self._crav_genres("shows")
            elif svc == "CRAV:movies":
                cats = self._crav_genres("movies")
            elif svc == "VM":
                cats = self._vm()
            elif svc == "ROKU":
                cats = self._roku()
            elif svc == "CBS":
                cats = self._cbs()
            elif svc == "CWTV":
                cats = self._cwtv()
            elif svc == "PBS":
                cats = self._pbs()
            elif svc == "CRAV":
                cats = self._crav()
            elif svc == "CBC":
                cats = self._cbc()
            elif svc == "ThreeNow":
                cats = self._threenow()
            elif svc == "AUBC":
                cats = self._aubc()
            elif svc == "SEVEN":
                cats = self._seven()
            elif svc == "TEN":
                cats = self._ten()
            elif svc == "RTE":
                cats = self._rte()
            elif svc == "SBS":
                cats = self._sbs()
            elif svc == "SBS:tv":
                cats = self._sbs_tv()
            elif svc == "SBS:movies":
                cats = self._sbs_movies()
            elif svc == "SEVEN:shows":
                cats = self._seven_shelves("shows")
            elif svc == "SEVEN:movies":
                cats = self._seven_shelves("movies")
            elif svc == "CBC:shows":
                cats = self._cbc_genres("shows")
            elif svc == "CBC:films":
                cats = self._cbc_genres("films")
            elif svc == "CBC:kids":
                cats = self._cbc_genres("kids")
            else:
                self.error.emit(
                    f"Category browsing is not yet supported for {svc}.\n"
                    f"Use Search instead, or paste a direct URL.")
                return
            if not cats:
                self.error.emit("No categories found for this service.")
            else:
                self.done.emit(cats)
        except Exception as exc:
            self.error.emit(f"Failed to fetch categories: {exc}")

    def _bbc(self) -> list:
        data = self._fetch_json(
            f"https://ibl.api.bbci.co.uk/ibl/v1/categories"
            f"?lang=en&api_key={self._API_KEY}"
        )
        return [
            {"name": c.get("title", c.get("id", "")), "id": c.get("id", "")}
            for c in data.get("categories", [])
            if c.get("id")
        ]

    def _all4(self) -> list:
        # channel4.com category page URLs
        return [
            {"name": "Film",                     "id": "https://www.channel4.com/categories/film"},
            {"name": "Documentary",              "id": "https://www.channel4.com/categories/documentaries"},
            {"name": "Comedy",                   "id": "https://www.channel4.com/categories/comedy"},
            {"name": "Drama",                    "id": "https://www.channel4.com/categories/drama"},
            {"name": "Entertainment",            "id": "https://www.channel4.com/categories/entertainment"},
            {"name": "Lifestyle",                "id": "https://www.channel4.com/categories/lifestyle"},
            {"name": "News & Current Affairs",   "id": "https://www.channel4.com/categories/news-current-affairs-and-politics"},
            {"name": "Sport",                    "id": "https://www.channel4.com/categories/sport"},
            {"name": "World Drama",              "id": "https://www.channel4.com/categories/world-drama"},
            {"name": "Box Sets",                 "id": "https://www.channel4.com/categories/boxsets"},
        ]

    def _itvx(self) -> list:
        # ITV collection page URLs
        return [
            {"name": "Films",                "id": "https://www.itv.com/watch/collections/make-it-a-movie-night/2CIASIVXkb4A6R1XxJ4s1f"},
            {"name": "Top Picks",            "id": "https://www.itv.com/watch/collections/top-picks/51Ry6KaT5pg9HYDJ8AqPwk"},
            {"name": "Gritty Thrillers",     "id": "https://www.itv.com/watch/collections/gritty-thrillers/5lTuwNT5hAkUyQJdPabiGT"},
            {"name": "True Life",            "id": "https://www.itv.com/watch/collections/true-life-drama/1nYAN4ipGU6L0qmgimh1lE"},
            {"name": "New",                  "id": "https://www.itv.com/watch/collections/fresh-in/7K0pfBiDFvOdBeHr7SnDzo"},
            {"name": "Comedy Drama",         "id": "https://www.itv.com/watch/collections/comedy-drama/5lADfSZ7PP5dNeJ6BCW5Gb"},
            {"name": "Comedy",               "id": "https://www.itv.com/watch/collections/show-me-the-funny/33idcDIeV32Cd3V7czYDeP"},
            {"name": "Entertainment",        "id": "https://www.itv.com/watch/collections/top-picks/4qcfuXnvuom6zss67k7e6p"},
            {"name": "Football",             "id": "https://www.itv.com/watch/collections/football/6iS0VOrNqLWQNCLL5T0nxL"},
            {"name": "Boxsets",              "id": "https://www.itv.com/watch/collections/unmissable-boxsets/5vk4Kkk8zRTiG3cWI698fR"},
            {"name": "Kids",                 "id": "https://www.itv.com/watch/collections/kids-top-picks/4ZSxLvMbULBxNbA9AqY96R"},
        ]

    def _my5(self) -> list:
        # My5 corona search API URLs with subgenre IDs
        return [
            {"name": "Films",               "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100117389032&vod_subgenres%5B%5D=6100117390032&vod_subgenres%5B%5D=6100117391032"},
            {"name": "Documentary",         "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres[]=6100110273032&vod_subgenres[]=6100105092032&vod_subgenres[]=6100105093032&vod_subgenres[]=6100105094032&vod_subgenres[]=6100105095032"},
            {"name": "Crime",               "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&limit=100&sort_by=popular&offset=0&vod_subgenres%5B%5D=7626766659032&vod_subgenres%5B%5D=7626766660032"},
            {"name": "Dramas & Soaps",      "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100110274032&vod_subgenres%5B%5D=6100110275032"},
            {"name": "Entertainment",       "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100112646032&vod_subgenres%5B%5D=6100110276032&vod_subgenres%5B%5D=6100110277032&vod_subgenres%5B%5D=6100112638032"},
            {"name": "Science and Nature",  "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100118658032&vod_subgenres%5B%5D=6100117395032&vod_subgenres%5B%5D=6100117396032"},
            {"name": "Sport",               "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100118660032"},
            {"name": "Travel",              "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100118664032&vod_subgenres%5B%5D=6100118662032&vod_subgenres%5B%5D=6100118663032"},
            {"name": "Real Lives",          "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100117393032"},
            {"name": "Lifestyle",           "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100114865032&vod_subgenres%5B%5D=6100114859032&vod_subgenres%5B%5D=6100114860032"},
            {"name": "News",                "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6726148277032"},
            {"name": "Milkshake",           "id": "https://corona.channel5.com/shows/search.json?platform=my5desktop&friendly=1&vod_subgenres%5B%5D=6100114867032"},
        ]

    def _uktv(self) -> list:
        # Genres and channels from u.co.uk
        return [
            {"name": "Comedy",              "id": "https://u.co.uk/genre/comedy"},
            {"name": "Documentaries",       "id": "https://u.co.uk/genre/documentaries"},
            {"name": "Drama",               "id": "https://u.co.uk/genre/drama"},
            {"name": "Entertainment",       "id": "https://u.co.uk/genre/entertainment"},
            {"name": "Lifestyle & Real Life","id": "https://u.co.uk/genre/lifestyle-and-real-life"},
            {"name": "── Channels ──",      "id": ""},
            {"name": "Dave",                "id": "https://u.co.uk/channel/dave"},
            {"name": "Drama",               "id": "https://u.co.uk/channel/drama"},
            {"name": "W",                   "id": "https://u.co.uk/channel/w"},
            {"name": "Yesterday",           "id": "https://u.co.uk/channel/yesterday"},
        ]

    def _plex(self) -> list:
        return [
            {"name": "Action & Adventure", "id": "1"},
            {"name": "Animation",           "id": "2"},
            {"name": "Comedy",              "id": "3"},
            {"name": "Documentary",         "id": "4"},
            {"name": "Drama",               "id": "5"},
            {"name": "Horror",              "id": "7"},
            {"name": "Science Fiction",     "id": "14"},
            {"name": "Thriller",            "id": "15"},
        ]

    def _stv(self) -> list:
        # player.stv.tv category page URLs
        return [
            {"name": "Films",               "id": "https://player.stv.tv/categories/movies"},
            {"name": "Sport",               "id": "https://player.stv.tv/categories/the-sport-hub"},
            {"name": "Crime Dramas",        "id": "https://player.stv.tv/categories/crime-drama"},
            {"name": "True Crime",          "id": "https://player.stv.tv/categories/crime-punishment"},
            {"name": "Comedy Dramas",       "id": "https://player.stv.tv/categories/comedy-drama"},
            {"name": "Documentaries",       "id": "https://player.stv.tv/categories/documentaries"},
            {"name": "Dramas",              "id": "https://player.stv.tv/categories/dramas"},
            {"name": "Entertainment",       "id": "https://player.stv.tv/categories/entertainment"},
            {"name": "Soaps",               "id": "https://player.stv.tv/categories/soaps"},
            {"name": "Food",                "id": "https://player.stv.tv/categories/food-lifestyle"},
            {"name": "Scenic Scotland",     "id": "https://player.stv.tv/categories/scenic-scotland"},
            {"name": "News",                "id": "https://player.stv.tv/categories/news-current-affairs"},
            {"name": "Thrillers",           "id": "https://player.stv.tv/categories/thrillers"},
            {"name": "History Hit",         "id": "https://player.stv.tv/categories/history-hit"},
            {"name": "Real Crime",          "id": "https://player.stv.tv/categories/real-crime"},
            {"name": "Real Stories",        "id": "https://player.stv.tv/categories/real-stories"},
            {"name": "Real Life",           "id": "https://player.stv.tv/categories/real-life"},
        ]

    def _tptv(self) -> list:
        # TPTV Encore genre list — id is passed as search query to CategoryShowsWorker
        return [
            {"name": "Crime",           "id": "crime"},
            {"name": "Drama",           "id": "drama"},
            {"name": "Comedy",          "id": "comedy"},
            {"name": "Mystery",         "id": "mystery"},
            {"name": "Thriller",        "id": "thriller"},
            {"name": "Horror",          "id": "horror"},
            {"name": "Science Fiction", "id": "science fiction"},
            {"name": "Western",         "id": "western"},
            {"name": "War",             "id": "war"},
            {"name": "Adventure",       "id": "adventure"},
            {"name": "Romance",         "id": "romance"},
            {"name": "Family",          "id": "family"},
            {"name": "Documentary",     "id": "documentary"},
            {"name": "Classic TV",      "id": "classic"},
        ]

    def _tvnz(self) -> list:
        # TVNZ edge API category URLs
        return [
            {"name": "Movies",              "id": "https://www.tvnz.co.nz/movies"},
            {"name": "Drama",               "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/drama"},
            {"name": "Home and Living",     "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/home-and-living"},
            {"name": "Sport Documentaries", "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/sport-documentaries"},
            {"name": "Natural World",       "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/natural-world"},
            {"name": "Foreign Language",    "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/foreign-language"},
            {"name": "True Crime",          "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/true-crime"},
            {"name": "Australian Drama",    "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/australian-drama"},
            {"name": "British Drama",       "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/british-drama"},
            {"name": "Sci-Fi & Fantasy",    "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/sci-fi-and-fantasy"},
            {"name": "Factual",             "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/factual"},
            {"name": "Comedy",              "id": "https://apis-edge-prod.tech.tvnz.co.nz/api/v1/web/play/page/categories/comedy"},
        ]

    def _pluto(self) -> list:
        import uuid as _uuid, urllib.parse as _up
        boot_qs = _up.urlencode({
            "appName": "web", "appVersion": "na",
            "clientID": str(_uuid.uuid1()), "deviceDNT": 0,
            "deviceId": "unknown", "clientModelNumber": "na",
            "serverSideAds": "false", "deviceMake": "unknown",
            "deviceModel": "web", "deviceType": "web",
            "deviceVersion": "unknown", "sid": str(_uuid.uuid1()),
        })
        boot  = self._fetch_json(f"https://boot.pluto.tv/v4/start?{boot_qs}")
        token = boot.get("sessionToken", "")
        params = _up.urlencode({"appName": "web", "appVersion": "na",
                                "clientID": str(_uuid.uuid1()), "deviceType": "web"})
        data = self._fetch_json(
            f"https://service-vod.clusters.pluto.tv/v3/vod/categories?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return [
            {"name": cat.get("name", cat.get("_id", "")), "id": cat.get("_id", "")}
            for cat in (data.get("categories") or [])
            if cat.get("_id") and cat.get("name")
        ]

    def _nine(self) -> list:
        import re as _re, urllib.request as _req, gzip as _gz
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
        }
        req = _req.Request("https://www.9now.com.au/genres", headers=hdrs)
        with _req.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            html = raw.decode("utf-8", errors="replace")
        # Genre links appear as href="/shows/{slug}" on the genres page
        slugs = _re.findall(r'href="/shows/([a-z0-9][a-z0-9-]+)"', html)
        seen: set = set()
        cats = []
        for slug in slugs:
            if slug in seen:
                continue
            seen.add(slug)
            cats.append({"name": slug.replace("-", " ").title(), "id": slug})
        return cats

    def _nbc(self) -> list:
        import re as _re, urllib.request as _req, gzip as _gz
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.nbc.com",
            "Referer": "https://www.nbc.com/",
        }
        req = _req.Request("https://www.nbc.com/shows", headers=hdrs)
        with _req.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            html = raw.decode("utf-8", errors="replace")
        # Category links appear as href="/shows/{slug}" on the NBC shows page
        slugs = _re.findall(r'href="/shows/([a-z0-9][a-z0-9-]+)"', html)
        seen: set = set()
        cats = []
        for slug in slugs:
            if slug in seen:
                continue
            seen.add(slug)
            cats.append({"name": slug.replace("-", " ").title(), "id": slug})
        return cats

    def _tubi(self) -> list:
        return [
            {"name": "Action",            "id": "https://tubitv.com/category/action"},
            {"name": "Animation",         "id": "https://tubitv.com/category/animation"},
            {"name": "Anime",             "id": "https://tubitv.com/category/anime"},
            {"name": "British TV",        "id": "https://tubitv.com/category/british_tv"},
            {"name": "Classic Movies",    "id": "https://tubitv.com/category/classic_movies"},
            {"name": "Comedy",            "id": "https://tubitv.com/category/comedy"},
            {"name": "Crime",             "id": "https://tubitv.com/category/crime"},
            {"name": "Documentary",       "id": "https://tubitv.com/category/documentary"},
            {"name": "Drama",             "id": "https://tubitv.com/category/drama"},
            {"name": "Family",            "id": "https://tubitv.com/category/family"},
            {"name": "Fantasy",           "id": "https://tubitv.com/category/fantasy"},
            {"name": "Horror",            "id": "https://tubitv.com/category/horror"},
            {"name": "International",     "id": "https://tubitv.com/category/international"},
            {"name": "Kids",              "id": "https://tubitv.com/category/kids"},
            {"name": "Romance",           "id": "https://tubitv.com/category/romance"},
            {"name": "Sci-Fi",            "id": "https://tubitv.com/category/sci_fi"},
            {"name": "Spanish",           "id": "https://tubitv.com/category/spanish"},
            {"name": "Thriller",          "id": "https://tubitv.com/category/thriller"},
            {"name": "True Crime",        "id": "https://tubitv.com/category/true_crime"},
            {"name": "Western",           "id": "https://tubitv.com/category/western"},
        ]

    def _rktn(self) -> list:
        return [
            {"name": "Movies",   "id": "rktn-type:movies"},
            {"name": "TV Shows", "id": "rktn-type:tv_shows"},
        ]

    def _rktn_genres(self, content_type: str) -> list:
        import urllib.parse as _up
        label_prefix = "Movies" if content_type == "movies" else "TV Shows"
        qs   = _up.urlencode({
            "classification_id": "18",
            "content_type":      content_type,
            "device_identifier": "web",
            "locale":            "en",
            "market_code":       "uk",
        })
        data = self._fetch_json(f"https://gizmo.rakuten.tv/v3/genres?{qs}")
        cats = []
        for genre in data.get("data") or []:
            if not isinstance(genre, dict):
                continue
            slug = genre.get("id", "")
            name = genre.get("name", slug)
            if not slug or slug == "--all":
                continue
            cats.append({"name": name, "id": f"{content_type}:{slug}"})
        return cats

    def _vm(self) -> list:
        import re as _re
        BASE = "key=821254297041614280861178657602&cc=IE&lang=en&platform=chrome"
        HEADERS = {
            "Origin":  "https://play.virginmediatelevision.ie",
            "Referer": "https://play.virginmediatelevision.ie/",
        }
        # Known playlists — used as fallback when home page links are geofenced
        KNOWN = [
            (435, "Trending Now"),
            (434, "Recently Added"),
            (248, "Boxsets"),
            (278, "Unmissable Drama"),
            (279, "Reality Check"),
            (280, "Virgin Media Originals"),
            (281, "True Crime & Investigation"),
            (287, "Star-Studded Shows"),
            (289, "Sport"),
            (293, "Food & Travel"),
            (329, "Documentaries"),
            (330, "Classic TV"),
            (336, "Chat and Entertainment"),
            (393, "Lets Get Quizzical"),
            (436, "True Crime Stories"),
        ]
        cats = []
        seen_ids = set()

        # Try dynamic discovery from home page (populated when in IE)
        try:
            home = self._fetch_json(
                f"https://v6-metadata-cf.simplestreamcdn.com/api/page/home?{BASE}", HEADERS
            )
            for s in (home.get("response") or {}).get("sections") or []:
                link = s.get("link") or ""
                m = _re.search(r"seriesPlaylist/(\d+)", link, _re.I)
                if m:
                    pid  = int(m.group(1))
                    name = (s.get("title") or f"Playlist {pid}").strip()
                    cats.append({"name": name, "id": f"vm-playlist:{pid}"})
                    seen_ids.add(pid)
        except Exception:
            pass

        # Hardcoded fallback for known playlists not discovered dynamically
        for pid, name in KNOWN:
            if pid not in seen_ids:
                cats.append({"name": name, "id": f"vm-playlist:{pid}"})

        # Catchup channels (always available)
        cats.extend([
            {"name": "── Catch Up ──",    "id": ""},
            {"name": "Virgin Media One",   "id": "vm-catchup:34806"},
            {"name": "Virgin Media Two",   "id": "vm-catchup:34824"},
            {"name": "Virgin Media Three", "id": "vm-catchup:34822"},
        ])
        return cats

    def _roku(self) -> list:
        # Tokens taken directly from therokuchannel.roku.com/genre/{token}/{slug}
        return [
            {"name": "Action",         "id": "roku-token:w.lmGLlWB7kAfG6ao3x"},
            {"name": "Animated",       "id": "roku-token:w.J9b7o1xY8mImylr10oky"},
            {"name": "Anime",          "id": "roku-token:w.D9P5xdADJlIBYJxw"},
            {"name": "Classic Cinema", "id": "roku-token:w.z1437WvRG9c56YlM1R3"},
            {"name": "Comedy",         "id": "roku-token:w.ZxGZeRpzbGslWKQL6"},
            {"name": "Crime",          "id": "roku-token:w.apGKP1mx2aubGrwl"},
            {"name": "Documentaries",  "id": "roku-token:w.45Ax2ewgv5CrP9yPP4m7IDZmQA"},
            {"name": "Drama",          "id": "roku-token:w.VyG3z1YKjQT5J4xV"},
            {"name": "Horror",         "id": "roku-token:w.J9b7o1xDM0tBVrRr3"},
            {"name": "Miniseries",     "id": "roku-token:w.RQGlM1KD4ZtJZkRbdJyDuaqwA"},
            {"name": "Music",          "id": "roku-token:w.v14l8W0omQc9122j"},
            {"name": "Reality",        "id": "roku-token:w.1ayDN3gjBqFg1x5Bda1"},
            {"name": "Romance",        "id": "roku-token:w.x14ymWlVGkc9VzKmqZa"},
            {"name": "Sci-Fi",         "id": "roku-token:w.RQGlM1KqJQTJrpRlQl4dUo1GwjjZbPum7"},
            {"name": "Sitcoms",        "id": "roku-token:w.5dxeaZRANmIJNNZvQ"},
            {"name": "Thrillers",      "id": "roku-token:w.QVGzk1v0MyUGwrg88lVphA1jWk9JJPH26pz5PBP1"},
            {"name": "Westerns",       "id": "roku-token:w.j5GmrWLJkVSoeNbyR9w"},
        ]

    def _cwtv(self) -> list:
        return [
            {"name": "All Shows", "id": "cwtv-genre:all"},
        ]

    def _cbs(self) -> list:
        return [
            {"name": "Popular",    "id": "cbs-cat:popular"},
            {"name": "A-Z",        "id": "cbs-cat:all"},
            {"name": "Dramas",     "id": "cbs-cat:dramas"},
            {"name": "Comedies",   "id": "cbs-cat:comedies"},
            {"name": "Reality",    "id": "cbs-cat:reality"},
            {"name": "Daytime",    "id": "cbs-cat:daytime"},
            {"name": "Primetime",  "id": "cbs-cat:primetime"},
            {"name": "Late Night", "id": "cbs-cat:late-night"},
            {"name": "Specials",   "id": "cbs-cat:specials"},
            {"name": "News",       "id": "cbs-cat:news"},
        ]

    def _pbs(self) -> list:
        # Genre values are confirmed GraphQL enum strings
        return [
            {"name": "Arts & Music",         "id": "pbs-genre:ARTS_AND_MUSIC"},
            {"name": "Culture",              "id": "pbs-genre:CULTURE"},
            {"name": "Drama",                "id": "pbs-genre:DRAMA"},
            {"name": "Food",                 "id": "pbs-genre:FOOD"},
            {"name": "History",              "id": "pbs-genre:HISTORY"},
            {"name": "Home & How To",        "id": "pbs-genre:HOME_AND_HOWTO"},
            {"name": "Indie Films",          "id": "pbs-genre:INDIE_FILMS"},
            {"name": "News & Public Affairs","id": "pbs-genre:NEWS_AND_PUBLIC_AFFAIRS"},
            {"name": "Science & Nature",     "id": "pbs-genre:SCIENCE_AND_NATURE"},
        ]

    def _crav(self) -> list:
        return [
            {"name": "Movies",   "id": "crav-type:movies"},
            {"name": "TV Shows", "id": "crav-type:shows"},
        ]

    def _crav_genres(self, section: str) -> list:
        if section == "shows":
            return [
                {"name": "Comedy",      "id": "crav-path:shows-comedy"},
                {"name": "Crime",       "id": "crav-path:shows-crime"},
                {"name": "Documentary", "id": "crav-path:shows-documentaries"},
                {"name": "Drama",       "id": "crav-path:shows-drama"},
                {"name": "Podcast",     "id": "crav-path:shows-podcast"},
                {"name": "Reality",     "id": "crav-path:shows-reality"},
                {"name": "Romance",     "id": "crav-path:shows-romance"},
            ]
        else:  # movies
            return [
                {"name": "Action & Adventure", "id": "crav-path:movies-action"},
                {"name": "Comedy",             "id": "crav-path:movies-comedy"},
                {"name": "Documentary",        "id": "crav-path:movies-documentaries"},
                {"name": "Drama",              "id": "crav-path:movies-drama"},
                {"name": "Horror",             "id": "crav-path:movies-horror"},
                {"name": "Romance",            "id": "crav-path:movies-romance"},
                {"name": "Sci-Fi & Fantasy",   "id": "crav-path:movies-sci-fi"},
            ]

    def _cbc(self) -> list:
        return [
            {"name": "TV Shows", "id": "cbc-type:shows"},
            {"name": "Films",  "id": "cbc-type:films"},
            {"name": "Kids",   "id": "cbc-type:kids"},
        ]

    def _cbc_genres(self, section: str) -> list:
        if section == "kids":
            return [
                {"name": "All",        "id": "cbc-genre:category/kids-all"},
                {"name": "2–5 Years",  "id": "cbc-genre:category/kids-2-5-years"},
                {"name": "6–8 Years",  "id": "cbc-genre:category/kids-6-8-years"},
                {"name": "9–12 Years", "id": "cbc-genre:category/kids-9-12-years"},
            ]
        elif section == "shows":
            return [
                {"name": "All",                  "id": "cbc-genre:category/shows"},
                {"name": "Drama",                "id": "cbc-genre:category/drama"},
                {"name": "Comedy",               "id": "cbc-genre:category/comedy"},
                {"name": "Lifestyle & Reality",  "id": "cbc-genre:category/lifestyle-reality"},
                {"name": "Mystery & Crime",      "id": "cbc-genre:category/mystery-and-crime"},
                {"name": "Sports",               "id": "cbc-genre:category/sports"},
                {"name": "News & Current Affairs","id": "cbc-genre:category/news-current-affairs"},
                {"name": "Music",                "id": "cbc-genre:category/music"},
            ]
        else:  # films
            return [
                {"name": "Drama",          "id": "cbc-collection:drama-films"},
                {"name": "Comedy",         "id": "cbc-collection:comedy-films"},
                {"name": "Romance",        "id": "cbc-collection:romance-films"},
                {"name": "RomCom",         "id": "cbc-collection:romcom-films"},
                {"name": "Horror",         "id": "cbc-collection:horror-films"},
                {"name": "Mystery & Crime","id": "cbc-collection:mystery-crime-films"},
                {"name": "Sci-Fi & Fantasy","id": "cbc-collection:sci-fi-fantasy-films"},
                {"name": "Music",          "id": "cbc-collection:music-films"},
                {"name": "Indigenous",     "id": "cbc-collection:indigenous-films"},
                {"name": "Watch with Pride","id": "cbc-collection:lgbtq-films"},
            ]

    def _threenow(self) -> list:
        return [
            {"name": "All Shows",           "id": "threenow-genre:all-shows"},
            {"name": "Comedy",              "id": "threenow-genre:comedy"},
            {"name": "Documentary",         "id": "threenow-genre:documentaries"},
            {"name": "Drama",               "id": "threenow-genre:drama"},
            {"name": "Factual",             "id": "threenow-genre:factual"},
            {"name": "Local",               "id": "threenow-genre:local"},
            {"name": "Movies",              "id": "threenow-genre:movies"},
            {"name": "News & Current Affairs","id": "threenow-genre:news-current-affairs"},
            {"name": "Paranormal",          "id": "threenow-genre:paranormal"},
            {"name": "Reality",             "id": "threenow-genre:reality"},
            {"name": "Sport",               "id": "threenow-genre:sport"},
            {"name": "Te reo Māori",        "id": "threenow-genre:te-reo-m-ori"},
            {"name": "True Crime",          "id": "threenow-genre:true-crime"},
        ]

    def _aubc(self) -> list:
        return [
            {"name": "Drama",         "id": "aubc-cat:drama"},
            {"name": "Comedy",        "id": "aubc-cat:comedy"},
            {"name": "Movies",        "id": "aubc-cat:movies"},
            {"name": "News",          "id": "aubc-cat:news"},
            {"name": "Sport",         "id": "aubc-cat:sport"},
            {"name": "Science & Nature", "id": "aubc-cat:science"},
            {"name": "Arts & Music",  "id": "aubc-cat:arts"},
            {"name": "Lifestyle",     "id": "aubc-cat:lifestyle"},
            {"name": "Education",     "id": "aubc-cat:education"},
            {"name": "Indigenous",    "id": "aubc-cat:indigenous"},
            {"name": "LGBTQIA+",      "id": "aubc-cat:lgbtqia"},
        ]

    def _seven(self) -> list:
        return [
            {"name": "TV Shows", "id": "seven-type:shows"},
            {"name": "Movies",   "id": "seven-type:movies"},
        ]

    def _seven_shelves(self, page_slug: str) -> list:
        import urllib.request as _ur, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "x-swm-apikey": "kGcrNnuPClrkynfnKwG8IA/NhVG6ut5nPEdWF2jscvE="}
        PARAMS = "platform-id=web&market-id=4&platform-version=5.25.0.0&api-version=5.9.0.0"
        req = _ur.Request(
            f"https://component-cdn.swm.digital/content/{page_slug}?{PARAMS}", headers=HDRS)
        with _ur.urlopen(req, timeout=15) as r:
            page = _json.loads(r.read().decode())
        if page_slug == "shows":
            # Use the Browse Categories navigation shelf — exact match to website
            for item in (page.get("items") or []):
                if not isinstance(item, dict): continue
                if item.get("type") == "navigationShelf" and (item.get("title") or "").lower() == "browse categories":
                    cats = []
                    for nav in (item.get("items") or []):
                        if not isinstance(nav, dict): continue
                        title  = nav.get("title") or ""
                        slug   = ((nav.get("contentLink") or {}).get("url") or "").lstrip("/")
                        if not title or not slug or slug == "shows-a-z":
                            continue
                        cats.append({"name": title, "id": f"seven-genre:{slug}"})
                    return cats
            return []
        else:
            # Movies — use the movie-specific mediaShelf items directly
            SKIP_MOVIES = {"new movies on 7plus", "popular movies", "christmas in july"}
            cats = []
            for item in (page.get("items") or []):
                if not isinstance(item, dict) or item.get("type") != "mediaShelf":
                    continue
                title    = item.get("title") or ""
                shelf_id = str(item.get("id") or "")
                if not title or not shelf_id or title.lower() in SKIP_MOVIES:
                    continue
                cats.append({"name": title, "id": f"seven-shelf:movies:{shelf_id}"})
            return cats

    def _ten(self) -> list:
        return [
            {"name": "Drama",              "id": "ten-genre:drama"},
            {"name": "Comedy",             "id": "ten-genre:comedy"},
            {"name": "Reality",            "id": "ten-genre:reality"},
            {"name": "Crime",              "id": "ten-genre:crime"},
            {"name": "Documentary",        "id": "ten-genre:documentary"},
            {"name": "Kids",               "id": "ten-genre:kids"},
            {"name": "Lifestyle",          "id": "ten-genre:lifestyle"},
            {"name": "Light Entertainment","id": "ten-genre:light-entertainment"},
            {"name": "News",               "id": "ten-genre:news"},
            {"name": "Sport",              "id": "ten-genre:sport"},
            {"name": "Adventure",          "id": "ten-genre:adventure"},
        ]

    def _rte(self) -> list:
        data = self._fetch_json(
            "https://feed.entertainment.tv.theplatform.eu/f/1uC-gC/rte-prd-prd-categories"
            "?range=1-100&schema=2.15&form=json"
        )
        return [
            {"name": e.get("title", ""), "id": e.get("title", "")}
            for e in (data.get("entries") or [])
            if e.get("title")
        ]

    def _sbs(self) -> list:
        return [
            {"name": "TV Shows", "id": "SBS:tv"},
            {"name": "Movies",   "id": "SBS:movies"},
        ]

    def _sbs_tv(self) -> list:
        return [
            {"name": "Drama",                  "id": "sbs-col:drama-tv-shows"},
            {"name": "Comedy",                 "id": "sbs-col:comedy-tv-shows"},
            {"name": "Documentary",            "id": "sbs-col:documentary-tv-shows"},
            {"name": "Food",                   "id": "sbs-col:food-tv-shows"},
            {"name": "Entertainment",          "id": "sbs-col:entertainment-tv-shows"},
            {"name": "Children's",             "id": "sbs-col:childrens-tv-shows"},
            {"name": "News & Current Affairs", "id": "sbs-col:news-and-current-affairs"},
            {"name": "NITV",                   "id": "sbs-page:nitv-muy-ngulayg"},
            {"name": "Sport",                  "id": "sbs-page:sport"},
        ]

    def _sbs_movies(self) -> list:
        return [
            {"name": "All",            "id": "sbs-movie:all-movies"},
            {"name": "Action",         "id": "sbs-movie:action-movies"},
            {"name": "Animation",      "id": "sbs-movie:animation-movies"},
            {"name": "Biography",      "id": "sbs-movie:biography-movies"},
            {"name": "Classic",        "id": "sbs-movie:classic-movies"},
            {"name": "Comedy",         "id": "sbs-movie:comedy-movies"},
            {"name": "Crime & Mystery","id": "sbs-movie:crime-mystery-movies"},
            {"name": "Drama",          "id": "sbs-movie:drama-movies"},
            {"name": "Family",         "id": "sbs-movie:family-movies"},
            {"name": "Fantasy",        "id": "sbs-movie:fantasy-movies"},
            {"name": "Documentary",    "id": "sbs-movie:feature-documentaries"},
            {"name": "Horror",         "id": "sbs-movie:horror-movies"},
            {"name": "Martial Arts",   "id": "sbs-movie:martial-arts-movies"},
            {"name": "Musical",        "id": "sbs-movie:musical-movies"},
            {"name": "Romance",        "id": "sbs-movie:romance-movies"},
            {"name": "Sci-Fi",         "id": "sbs-movie:sci-fi-cinema"},
            {"name": "Thriller",       "id": "sbs-movie:thriller-movies"},
            {"name": "War",            "id": "sbs-movie:war-movies"},
            {"name": "Western",        "id": "sbs-movie:wild-westerns-movies"},
        ]


class CategoryShowsWorker(QThread):
    """Fetches the list of shows in a given category."""
    done  = pyqtSignal(list)   # list of {title, url, synopsis}
    error = pyqtSignal(str)

    _API_KEY = "D2FgtcTxGqqIgLsfBWTJdrQh2tVdeaAp"

    def __init__(self, service_id: str, category_id: str, category_name: str):
        super().__init__()
        self._svc         = service_id
        self._id          = category_id
        self._name        = category_name
        self.total_count  = 0   # set by _rktn() when API provides a total

    def _fetch_json(self, url: str, headers: dict | None = None) -> dict | list:
        import urllib.request, gzip as _gz, json as _json
        hdrs = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            text = raw.decode("utf-8")
            return _json.loads(text) if text.strip() else {}

    def run(self):
        try:
            svc = self._svc
            if svc == "iP":
                shows = self._bbc()
            elif svc == "ALL4":
                shows = self._all4()
            elif svc == "ITV":
                shows = self._itvx()
            elif svc == "MY5":
                shows = self._my5()
            elif svc == "UKTV":
                shows = self._uktv()
            elif svc == "STV":
                shows = self._stv()
            elif svc == "TVNZ":
                shows = self._tvnz()
            elif svc == "TPTV":
                shows = self._tptv()
            elif svc == "TUBI":
                shows = self._tubi()
            elif svc == "PLUTO":
                shows = self._pluto()
            elif svc == "NINE":
                shows = self._nine()
            elif svc == "NBC":
                shows = self._nbc()
            elif svc == "RKTN":
                shows = self._rktn()
            elif svc == "VM":
                shows = self._vm()
            elif svc == "ROKU":
                shows = self._roku()
            elif svc == "CBS":
                shows = self._cbs()
            elif svc == "CWTV":
                shows = self._cwtv()
            elif svc == "PBS":
                shows = self._pbs()
            elif svc == "CRAV":
                shows = self._crav()
            elif svc == "CBC":
                shows = self._cbc()
            elif svc == "ThreeNow":
                shows = self._threenow()
            elif svc == "AUBC":
                shows = self._aubc()
            elif svc == "SEVEN":
                shows = self._seven()
            elif svc == "TEN":
                shows = self._ten()
            elif svc == "SBS":
                shows = self._sbs()
            elif svc == "RTE":
                shows = self._rte()
            else:
                self.error.emit(f"Category shows not available for {svc}.")
                return
            if not shows:
                self.error.emit("No shows found in this category.")
            else:
                self.done.emit(shows)
        except Exception as exc:
            self.error.emit(f"Failed to fetch category shows: {exc}")

    def _bbc(self) -> list:
        shows = []
        seen = set()
        page = 1
        per_page = 100
        while True:
            data = self._fetch_json(
                f"https://ibl.api.bbci.co.uk/ibl/v1/categories/{self._id}/programmes"
                f"?rights=web&availability=available&per_page={per_page}&page={page}&api_key={self._API_KEY}"
            )
            cat = data.get("category_programmes") or {}
            elements = cat.get("elements") or []
            for item in elements:
                pid = item.get("id", "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                shows.append({
                    "title":    item.get("title", pid),
                    "synopsis": (item.get("synopses") or {}).get("small", ""),
                    "url":      f"https://www.bbc.co.uk/iplayer/brand/{pid}",
                })
            if len(elements) < per_page:
                break
            page += 1
            if page > 20:
                break
        return shows

    def _fetch_html(self, url: str, headers: dict | None = None) -> str:
        import urllib.request, gzip as _gz
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,*/*",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-GB,en;q=0.9",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            return raw.decode("utf-8", errors="replace")

    def _all4(self) -> list:
        # _id is a channel4.com/categories/{slug} page URL — parse __PARAMS__ for brand list
        import re as _re, json as _json
        html = self._fetch_html(self._id, headers={
            "Accept": "*/*",
            "Origin": "https://www.channel4.com",
            "Referer": "https://www.channel4.com/",
        })
        flat = html.replace("‌", "").replace("\r\n", "").replace("undefined", "null")
        m = _re.search(r"<script>window\.__PARAMS__ = ", flat)
        if not m:
            return []
        decoder = _json.JSONDecoder()
        data, _ = decoder.raw_decode(flat, m.end())
        items = (data.get("initialData") or {}).get("brands", {}).get("items") or []
        shows = []
        for item in items:
            href  = item.get("hrefLink") or ""
            title = item.get("labelText") or item.get("title") or ""
            if not href or not title:
                continue
            url = f"https://www.channel4.com{href}" if not href.startswith("http") else href
            shows.append({"title": title, "synopsis": item.get("overlayText", ""), "url": url})
        return shows

    def _itvx(self) -> list:
        # _id is an ITV collection page URL — parse __NEXT_DATA__ for collection.shows
        import re as _re, json as _json
        html = self._fetch_html(self._id, headers={
            "user-agent": "Dalvik/2.9.8 (Linux; U; Android 9.9.2; ALE-L94 Build/NJHGGF)",
            "host": "www.itv.com",
        })
        m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>', html, _re.DOTALL)
        if not m:
            return []
        data = _json.loads(m.group(1))
        shows_raw = ((data.get("props") or {}).get("pageProps") or {}).get("collection", {}).get("shows") or []
        shows = []
        for item in shows_raw:
            if not isinstance(item, dict):
                continue
            title_slug = item.get("titleSlug", "")
            prog_id    = (item.get("encodedProgrammeId") or {}).get("letterA", "")
            ep_id      = (item.get("encodedEpisodeId") or {}).get("letterA", "")
            title      = title_slug.replace("-", " ").title()
            if not prog_id:
                continue
            shows.append({
                "title":    title,
                "synopsis": item.get("description", ""),
                "url":      f"https://www.itv.com/watch/{title_slug}/{prog_id}/{ep_id}",
            })
        return shows

    def _my5(self) -> list:
        # _id is the full corona search URL — fetch and parse shows array directly
        data = self._fetch_json(self._id)
        shows = []
        for s in (data.get("shows") or []):
            if not isinstance(s, dict):
                continue
            f_name = s.get("f_name", "")
            if not f_name:
                continue
            shows.append({
                "title":    s.get("title", f_name),
                "synopsis": s.get("s_desc", ""),
                "url":      f"https://corona.channel5.com/shows/{f_name}/seasons.json?platform=my5desktop&friendly=1",
            })
        return shows

    def _uktv(self) -> list:
        # _id is a u.co.uk/genre/... or u.co.uk/channel/... page
        if not self._id:
            return []
        import re as _re
        html = self._fetch_html(self._id, headers={
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,*/*",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        # Extract unique show slugs from /shows/{slug}/watch-online links in the page
        slugs = list(dict.fromkeys(
            _re.findall(r'/shows/([a-z0-9-]+)/watch-online', html)
        ))
        if not slugs:
            return []
        # Fetch each brand from vschedules API to get title + synopsis
        hdrs = {"user-agent": "okhttp/4.7.2"}
        shows = []
        for slug in slugs:
            try:
                data = self._fetch_json(
                    f"https://vschedules.uktv.co.uk/vod/brand/?slug={slug}", hdrs)
                title = data.get("name") or data.get("title") or data.get("brand_title") or slug
                shows.append({
                    "title":    title,
                    "synopsis": data.get("synopsis", ""),
                    "url":      f"https://vschedules.uktv.co.uk/vod/brand/?slug={slug}",
                })
            except Exception:
                # Still add it with just the slug as title so it's selectable
                shows.append({
                    "title":    slug.replace("-", " ").title(),
                    "synopsis": "",
                    "url":      f"https://vschedules.uktv.co.uk/vod/brand/?slug={slug}",
                })
        return shows

    def _stv(self) -> list:
        # _id is a player.stv.tv/categories/{slug} page URL
        # Parses __NEXT_DATA__ → props.pageProps.data.assets
        import re as _re, json as _json
        html = self._fetch_html(self._id, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "Origin": "https://player.stv.tv",
            "Referer": "https://player.stv.tv/",
        })
        m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>', html, _re.DOTALL)
        if not m:
            return []
        data = _json.loads(m.group(1))
        assets = ((data.get("props") or {}).get("pageProps") or {}).get("data", {}).get("assets") or []
        shows = []
        for item in assets:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            link  = item.get("link", "")
            if not title or not link:
                continue
            url = f"https://player.stv.tv{link}" if not link.startswith("http") else link
            shows.append({
                "title":    title,
                "synopsis": item.get("description", ""),
                "url":      url,
            })
        return shows

    def _tptv(self) -> list:
        # _id is a genre search term; use the same search API as SearchWorker._tptv
        import sys as _sys, json as _json
        from pathlib import Path as _P
        cfg = load_config()
        _venv_sp = _P(cfg.get("install_dir", "")) / ".venv" / "Lib" / "site-packages"
        if _venv_sp.exists() and str(_venv_sp) not in _sys.path:
            _sys.path.insert(0, str(_venv_sp))
        import httpx as _httpx
        import urllib.parse as _up
        api_key = "zq5pyPd0RTbNg3Fyj52PrkKL9c2Af38HHh4itgZTKDaCzjAyhd"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "api-key": api_key,
            "tenant": "encore",
            "Origin": "https://tptvencore.co.uk",
            "Referer": "https://tptvencore.co.uk/",
        }
        qt = _up.quote(self._id)
        with _httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(f"https://tptvencore.co.uk/api/core/search?q={qt}&page=1&pageSize=40&locale=en", headers=hdrs)
        if r.status_code != 200:
            raise RuntimeError(f"TPTV genre search failed: HTTP {r.status_code}")
        shows = []
        for item in (r.json().get("data") or []):
            if not isinstance(item, dict):
                continue
            title    = item.get("title", "")
            playback = (item.get("video") or {}).get("playback", "")
            if not title or not playback:
                continue
            playback = playback.replace("api/core/play", "playback")
            vid_id   = playback.rstrip("/").split("/")[-1].replace("?locale=en", "")
            url      = f"https://tptvencore.co.uk/playback/item/{vid_id}"
            synopsis = (item.get("description") or "").replace("\n", " ")[:200]
            shows.append({"title": title, "synopsis": synopsis, "url": url})
        return shows

    def _tvnz(self) -> list:
        # The TVNZ category edge API is unavailable unauthenticated — derive a
        # search term from the category URL slug and use the search API instead.
        import urllib.parse as _up
        path = _up.urlparse(self._id).path.rstrip("/")
        slug = path.split("/")[-1]

        # Movies use cty=="movie" and a different URL pattern
        is_movies = (slug == "movies")
        cty_filter = "movie" if is_movies else "tvseries"
        term = "movie" if is_movies else slug.replace("-", " ").replace("_", " ").strip()
        if not term:
            return []

        HDRS = {
            "x-device-type": "androidtv",
            "x-app-store-type": "androidtv",
            "x-client-id": "tvnz-tvnz-androidtv",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; Android TV Build/RTMA.250416.082)",
        }
        shows = []
        seen = set()
        for page in range(1, 20):
            params = _up.urlencode({
                "mode": "detail",
                "st": "published",
                "term": term,
                "pageNumber": str(page),
                "pageSize": "50",
                "reg": "nz",
                "dt": "androidtv",
                "client": "tvnz-tvnz-androidtv",
                "pf": "Regular",
                "allowpg": "true",
            })
            data = self._fetch_json(
                f"https://search-cdn.cms-api.tvnz.co.nz/content/search?{params}", HDRS)
            if data.get("header", {}).get("message", "").lower() != "success":
                break
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("cty") != cty_filter:
                    continue
                nu    = item.get("nu", "")
                title = (item.get("lon") or [{"n": ""}])[0].get("n", "")
                synopsis = (item.get("losd") or [{"n": ""}])[0].get("n", "")
                if not title or not nu or nu in seen:
                    continue
                seen.add(nu)
                url_base = "movies" if is_movies else "tvseries"
                shows.append({
                    "title":    title,
                    "synopsis": synopsis,
                    "url":      f"https://tvnz.co.nz/{url_base}/{nu}",
                })
            hdr = data.get("header", {})
            total = hdr.get("count", 0)
            if page * 50 >= total:
                break
        return sorted(shows, key=lambda s: s["title"])

    def _pluto(self) -> list:
        import uuid as _uuid, urllib.parse as _up
        boot_qs = _up.urlencode({
            "appName": "web", "appVersion": "na",
            "clientID": str(_uuid.uuid1()), "deviceDNT": 0,
            "deviceId": "unknown", "clientModelNumber": "na",
            "serverSideAds": "false", "deviceMake": "unknown",
            "deviceModel": "web", "deviceType": "web",
            "deviceVersion": "unknown", "sid": str(_uuid.uuid1()),
        })
        boot   = self._fetch_json(f"https://boot.pluto.tv/v4/start?{boot_qs}")
        token  = boot.get("sessionToken", "")
        region = boot.get("session", {}).get("activeRegion", "").lower()
        params = _up.urlencode({"appName": "web", "appVersion": "na",
                                "clientID": str(_uuid.uuid1()), "deviceType": "web"})
        data = self._fetch_json(
            f"https://service-vod.clusters.pluto.tv/v3/vod/categories/{self._id}/items?{params}",
            {"Authorization": f"Bearer {token}"},
        )
        base = f"https://pluto.tv/{region}/on-demand" if region else "https://pluto.tv/on-demand"
        shows = []
        for item in (data.get("items") or []):
            item_id = item.get("_id", "")
            kind    = item.get("type", "series")
            title   = item.get("name", item_id)
            slug    = item.get("slug", item_id)
            if not item_id:
                continue
            url = f"{base}/{'movies' if kind == 'film' else 'series'}/{item_id}/{slug}/details"
            shows.append({
                "title":    title,
                "synopsis": item.get("description", ""),
                "url":      url,
            })
        return shows

    @staticmethod
    def _tubi_cookies() -> tuple[str, str | None]:
        """Read tubitv.com cookies from Tubi.txt; raises RuntimeError with user message if unusable."""
        import base64 as _b64, json as _json, time as _time
        from pathlib import Path as _P
        cfg = load_config()
        cookie_file = _P(cfg.get("install_dir", "")) / "Cookies" / "Tubi.txt"
        if not cookie_file.exists():
            raise RuntimeError(
                "Tubi search requires a cookie file.\n"
                "Log in to tubitv.com in your browser, export cookies to a file named "
                "Tubi.txt and place it in EnvyCore/Cookies/."
            )
        pairs: list[str] = []
        at_token: str | None = None
        try:
            for line in cookie_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, name, value = parts[0], parts[5], parts[6]
                if "tubitv.com" not in domain:
                    continue
                pairs.append(f"{name}={value}")
                if name == "at":
                    at_token = value
        except Exception as exc:
            raise RuntimeError(f"Could not read Tubi.txt: {exc}") from exc
        if not pairs:
            raise RuntimeError(
                "Tubi.txt does not contain any tubitv.com cookies.\n"
                "Log in to tubitv.com in your browser and re-export the cookies."
            )
        if at_token:
            try:
                payload_b64 = at_token.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                exp = _json.loads(_b64.urlsafe_b64decode(payload_b64)).get("exp", 0)
                if exp and exp < _time.time():
                    raise RuntimeError(
                        "Your Tubi cookie (at= token) has expired.\n"
                        "Log in to tubitv.com in your browser and re-export the cookies, "
                        "then replace EnvyCore/Cookies/Tubi.txt with the new file."
                    )
            except RuntimeError:
                raise
            except Exception:
                pass
        return "; ".join(pairs), at_token

    def _tubi(self) -> list:
        import urllib.parse as _up
        cookie_hdr, at_token = self._tubi_cookies()
        params = _up.urlencode({
            "search":           self._name,
            "include_linear":   "false",
            "include_channels": "false",
            "is_kids_mode":     "false",
        })
        hdrs = {"Cookie": cookie_hdr}
        if at_token:
            hdrs["Authorization"] = f"Bearer {at_token}"
        data = self._fetch_json(
            f"https://search.production-public.tubi.io/api/v2/search?{params}",
            hdrs,
        )
        shows = []
        for item in (data.get("contents") or {}).values():
            content_id = item.get("id", "")
            kind_code  = item.get("type", "s")
            if not content_id:
                continue
            url = (f"https://tubitv.com/movies/{content_id}"
                   if kind_code == "v" else
                   f"https://tubitv.com/series/{content_id}")
            shows.append({
                "title":    item.get("title", content_id),
                "synopsis": item.get("description", ""),
                "url":      url,
            })
        return shows

    def _nine(self) -> list:
        import re as _re, urllib.request as _req, gzip as _gz
        # Genre pages are server-rendered HTML at /shows/{genre-slug}
        # Show links appear as href="/show-slug" (single path segment)
        _NON_SHOW = {
            "about", "contact", "genres", "help", "live", "login", "news",
            "privacy", "search", "shows", "terms", "ways-to-watch",
        }
        genre_slug = self._id  # e.g. "comedy", "drama"
        url = f"https://www.9now.com.au/shows/{genre_slug}"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
        }
        req = _req.Request(url, headers=hdrs)
        with _req.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            html = raw.decode("utf-8", errors="replace")

        import html as _html
        slugs = _re.findall(r'href="(/([a-z0-9][a-z0-9-]+))"', html)
        seen: set = set()
        shows = []
        for full_path, slug in slugs:
            if slug in _NON_SHOW or slug in seen:
                continue
            seen.add(slug)
            title = slug.replace("-", " ").title()
            # Try to extract display name from nearby h2 tag
            m = _re.search(
                rf'href="{_re.escape(full_path)}".*?<h2[^>]*>([^<]+)</h2>',
                html, _re.DOTALL
            )
            if m:
                title = _html.unescape(m.group(1).strip())
            shows.append({
                "title":    title,
                "synopsis": "",
                "url":      f"https://www.9now.com.au{full_path}",
            })
        return shows

    def _nbc(self) -> list:
        import re as _re, urllib.request as _req, gzip as _gz, html as _html
        # Category pages are server-rendered at /shows/{genre-slug}
        # Show links appear as href="/{show-slug}" (single path segment)
        _NON_SHOW = {
            "about", "brand", "contact", "help", "live", "login", "news",
            "privacy", "search", "shows", "schedule", "terms", "video",
        }
        url = f"https://www.nbc.com/shows/{self._id}"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.nbc.com",
            "Referer": "https://www.nbc.com/",
        }
        req = _req.Request(url, headers=hdrs)
        with _req.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            html_text = raw.decode("utf-8", errors="replace")
        slugs = _re.findall(r'href="/([a-z0-9][a-z0-9-]+)"', html_text)
        seen: set = set()
        shows = []
        for slug in slugs:
            if slug in _NON_SHOW or slug in seen:
                continue
            seen.add(slug)
            title = slug.replace("-", " ").title()
            m = _re.search(
                rf'href="/{_re.escape(slug)}".*?<[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)<',
                html_text, _re.DOTALL | _re.IGNORECASE
            )
            if m:
                title = _html.unescape(m.group(1).strip())
            shows.append({
                "title":    title,
                "synopsis": "",
                "url":      f"https://www.nbc.com/{slug}",
            })
        return shows

    def _rktn_fetch(self, url: str) -> dict:
        import urllib.request as _ur, json as _json, re as _re
        req = _ur.Request(url, headers={
            "Accept":     "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin":     "https://www.rakuten.tv",
            "Referer":    "https://www.rakuten.tv/",
        })
        with _ur.urlopen(req, timeout=45) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        # Strip unescaped ASCII control chars that Rakuten embeds in plot descriptions
        text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return _json.loads(text)

    def _rktn(self) -> list:
        import urllib.parse as _up
        # self._id is "movies:{genre_slug}" or "tv_shows:{genre_slug}"
        try:
            content_type, genre_slug = self._id.split(":", 1)
        except ValueError:
            return []
        base_params = {
            "classification_id": "18",
            "content_type":      content_type,
            "device_identifier": "web",
            "locale":            "en",
            "market_code":       "uk",
            "per_page":          "36",
        }
        shows      = []
        seen       = set()
        total_pages = 1
        try:
            # Page 1 — genre metadata endpoint includes first batch in data.contents.data
            qs1   = _up.urlencode({**base_params, "contents[per_page]": "36"})
            data1 = self._rktn_fetch(f"https://gizmo.rakuten.tv/v3/genres/{genre_slug}?{qs1}")
            genre_obj    = data1.get("data") or {}
            contents_obj = genre_obj.get("contents") if isinstance(genre_obj, dict) else {}
            if isinstance(contents_obj, dict):
                items1      = contents_obj.get("data") or []
                pagination        = (contents_obj.get("meta") or {}).get("pagination") or {}
                total_pages       = int(pagination.get("total_pages") or 1)
                self.total_count  = int(pagination.get("count") or 0)
            else:
                items1 = contents_obj if isinstance(contents_obj, list) else []

            def _add(items):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    permalink = item.get("id") or item.get("permalink", "")
                    if not permalink or permalink in seen:
                        continue
                    seen.add(permalink)
                    shows.append({
                        "title":    item.get("title", permalink),
                        "synopsis": item.get("short_plot") or item.get("plot", ""),
                        "url":      f"https://www.rakuten.tv/uk/{content_type}/{permalink}",
                    })

            _add(items1)

            # Pages 2+ — separate /contents endpoint with plain page= param
            # Cap at 5 pages (180 titles) to avoid rate-limiting on large genres
            import time as _time
            for page in range(2, min(total_pages, 5) + 1):
                _time.sleep(0.4)
                qs   = _up.urlencode({**base_params, "page": str(page)})
                data = self._rktn_fetch(
                    f"https://gizmo.rakuten.tv/v3/genres/{genre_slug}/contents?{qs}"
                )
                # Response is {"data": [...items...], "meta": {...}}
                items = data.get("data") or []
                if not isinstance(items, list):
                    items = []
                _add(items)
                if len(items) < 36:
                    break
        except Exception as exc:
            raise RuntimeError(f"Rakuten TV API error: {exc}") from exc
        return shows

    def _vm(self) -> list:
        import re as _re
        BASE = "key=821254297041614280861178657602&cc=IE&lang=en&platform=chrome"
        HEADERS = {
            "Origin":  "https://play.virginmediatelevision.ie",
            "Referer": "https://play.virginmediatelevision.ie/",
        }

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "show"

        cat_id = self._id

        if cat_id.startswith("vm-playlist:"):
            playlist_id = cat_id.split(":", 1)[1]
            data = self._fetch_json(
                f"https://v6-metadata-cf.simplestreamcdn.com/api/series/playlist/{playlist_id}?{BASE}",
                HEADERS,
            )
            shows = []
            seen  = set()
            for section in (data.get("response") or {}).get("sections") or []:
                uuid = section.get("id") or ""
                if not uuid or uuid in seen:
                    continue
                seen.add(uuid)
                title    = section.get("title") or uuid
                synopsis = section.get("description") or ""
                shows.append({
                    "title":    title,
                    "synopsis": synopsis,
                    "url":      f"https://play.virginmediatelevision.ie/shows/{uuid}/{_slugify(title)}",
                })
            return shows

        if cat_id.startswith("vm-catchup:"):
            channel_id = cat_id.split(":", 1)[1]
            data = self._fetch_json(
                f"https://v6-metadata-cf.simplestreamcdn.com/api/vod/{channel_id}?{BASE}",
                HEADERS,
            )
            episodes = []
            seen     = set()
            for section in (data.get("response") or {}).get("sections") or []:
                for tile in section.get("tiles") or []:
                    uvid = str(tile.get("uvid") or tile.get("id") or "")
                    if not uvid or uvid in seen:
                        continue
                    seen.add(uvid)
                    title    = tile.get("title") or f"Episode {uvid}"
                    synopsis = tile.get("description") or ""
                    episodes.append({
                        "title":    title,
                        "synopsis": synopsis,
                        "url":      f"https://play.virginmediatelevision.ie/watch/vod/{uvid}/{_slugify(title)}",
                    })
            return episodes

        return []

    def _roku(self) -> list:
        import re as _re, urllib.request as _ur, json as _json, http.cookiejar as _cj, urllib.parse as _up
        HDRS = {
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          "https://therokuchannel.roku.com",
            "Referer":         "https://therokuchannel.roku.com/",
        }
        jar    = _cj.CookieJar()
        opener = _ur.build_opener(_ur.HTTPCookieProcessor(jar))
        CONTENT_BASE = "https://therokuchannel.roku.com/api/v2/homescreen/content/"

        def _get(url):
            req = _ur.Request(url, headers=HDRS)
            with opener.open(req, timeout=20) as r:
                raw = r.read()
                if not raw.strip():
                    raise RuntimeError("Roku returned empty — try a US VPN")
                return _json.loads(raw.decode("utf-8"))

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "title"

        if self._id.startswith("roku-token:"):
            page_token = self._id.split(":", 1)[1]
        elif self._id.startswith("roku-genre:"):
            genre_slug = self._id.split(":", 1)[1]
            # Homepage visit to set session cookies
            opener.open(_ur.Request("https://therokuchannel.roku.com/",
                                    headers={**HDRS, "Accept": "text/html,*/*"}), timeout=20)
            # Fetch category metadata — meta.id is the signed page token (w.TOKEN)
            cat_data   = _get(CONTENT_BASE + _up.quote(
                f"https://content.sr.roku.com/content/v1/roku-trc/cat-{genre_slug}", safe=""))
            page_token = (cat_data.get("meta") or {}).get("id", "")
            if not page_token or not page_token.startswith("w."):
                raise RuntimeError(f"No page token found for Roku genre '{genre_slug}'")
        else:
            return []

        # Homepage visit to set session cookies (roku-token path skips the genre lookup above)
        if self._id.startswith("roku-token:"):
            opener.open(_ur.Request("https://therokuchannel.roku.com/",
                                    headers={**HDRS, "Accept": "text/html,*/*"}), timeout=20)

        # Fetch the rendered genre page
        data = _get(
            f"https://therokuchannel.roku.com/api/v2/homescreen/pages/{page_token}/rendered")

        # 4. Parse collections[].view[] — each item has details.href with tpl_id={content_id}
        shows = []
        seen  = set()
        for collection in data.get("collections") or []:
            for item in collection.get("view") or []:
                details = item.get("details") or {}
                href    = details.get("href") or ""
                cid_m   = _re.search(r"tpl_id=([a-z0-9]+)", href)
                if not cid_m:
                    continue
                cid = cid_m.group(1)
                if cid in seen:
                    continue
                seen.add(cid)
                content  = item.get("content") or {}
                ctype    = content.get("type", "")
                title    = content.get("title") or cid
                type_seg = "movie" if ctype in ("movie", "tvspecial") else "series"
                shows.append({
                    "title":    title,
                    "synopsis": "",
                    "url":      f"https://therokuchannel.roku.com/details/{cid}/{type_seg}/{_slugify(title)}",
                })
        return shows

    def _cwtv(self) -> list:
        import urllib.request as _ur, json as _json
        if not self._id.startswith("cwtv-genre:"):
            return []
        genre = self._id.split(":", 1)[1]
        HDRS  = {"User-Agent": "Mozilla/5.0 (Linux; Android 11; Smart TV Build/AR2101; wv)",
                 "Accept": "application/json"}
        # Fetch full show listing and filter by genre
        req = _ur.Request(
            "https://data.cwtv.com/feed/app-2/shows/type_shows/apiversion_25/device_androidtv",
            headers=HDRS,
        )
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8"))
        # API returns genres as comma-separated strings inside a list, e.g. ["Action,Sci-Fi"]
        # Map our slug IDs to keywords that appear in those strings
        GENRE_KEYWORDS = {
            "drama":            ["drama"],
            "comedy":           ["comedy"],
            "action-adventure": ["action", "adventure"],
            "sci-fi-fantasy":   ["sci-fi", "sci fi", "fantasy"],
            "crime":            ["crime"],
            "reality":          ["reality"],
            "competition":      ["competition"],
            "supernatural":     ["supernatural"],
        }
        keywords = GENRE_KEYWORDS.get(genre, [genre]) if genre != "all" else []
        shows = []
        for item in data.get("items") or []:
            slug  = item.get("show_slug") or item.get("slug") or ""
            title = item.get("title") or item.get("show_title") or slug
            if not slug:
                continue
            if keywords:
                # Flatten comma-separated genre strings into individual parts
                raw = item.get("genres") or []
                parts = []
                for g in raw:
                    for p in g.split(","):
                        parts.append(p.strip().lower())
                if not any(kw in p for kw in keywords for p in parts):
                    continue
            shows.append({
                "title":    title,
                "synopsis": item.get("description_long") or item.get("description") or "",
                "url":      f"https://www.cwtv.com/shows/{slug}",
            })
        return shows

    def _cbs(self) -> list:
        import urllib.request as _ur, json as _json
        if not self._id.startswith("cbs-cat:"):
            return []
        slug = self._id.split(":", 1)[1]   # e.g. "dramas", "all", "late-night"
        HDRS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept":     "application/json, text/plain, */*",
            "Referer":    "https://www.cbs.com/shows/",
        }
        req  = _ur.Request(f"https://www.cbs.com/shows_xhr/{slug}/", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8"))
        shows = []
        seen  = set()
        # Structure: result.data[].result.data[] → show items
        for group in (data.get("result") or {}).get("data") or []:
            inner = (group.get("result") or {}).get("data") or []
            for show in inner:
                href  = show.get("href") or ""
                title = show.get("title") or href
                if not href or href in seen:
                    continue
                seen.add(href)
                url = href if href.startswith("http") else f"https://www.cbs.com{href}"
                shows.append({"title": title, "synopsis": "", "url": url})
        return shows

    def _pbs(self) -> list:
        if not self._id.startswith("pbs-genre:"):
            return []
        genre = self._id.split(":", 1)[1]  # already uppercase enum e.g. "DRAMA"
        return SearchWorker._pbs_graphql(genre=genre, first=50, ordering="POPULAR", paginate=True)

    def _crav(self) -> list:
        import urllib.request as _ur, json as _json
        if not self._id.startswith("crav-path:"):
            return []
        slug = self._id.split(":", 1)[1]  # e.g. "shows-drama", "movies-action", "kids"

        token = _crav_get_graphql_token()
        if not token:
            raise RuntimeError("No CRAV credentials found. Add CRAV credentials to envied.yaml.")

        HDRS = {
            "User-Agent":    "Dalvik/2.1.0 (Linux; U; Android 11; SHIELD Android TV Build/RQ1A.210105.003)",
            "Content-Type":  "application/json",
            "Accept":        "*/*",
            "authorization": f"Bearer {token}",
        }
        SESSION_CTX = {"userMaturity": "ADULT", "userLanguage": "EN"}

        def _gql(payload):
            req = _ur.Request("https://rte-api.bellmedia.ca/graphql",
                              data=_json.dumps(payload).encode(), headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                return _json.loads(r.read().decode())

        # 1. GetItemByPath → screen ID
        item_data = _gql({
            "query": "query GetItemByPath($path:String!){itemByPath(path:$path){id type}}",
            "variables": {"path": slug},
        })
        screen_id = ((item_data.get("data") or {}).get("itemByPath") or {}).get("id") or ""
        if not screen_id:
            return []

        # 2. GetScreen → first Container ID
        screen_data = _gql({
            "query": (
                "query GetScreen($sessionContext:SessionContext!,$id:String!,$limit:Int,$cursor:String){"
                "screen(sessionContext:$sessionContext id:$id){"
                "screenContentsPage(limit:$limit cursor:$cursor){"
                "screenContents{__typename ...on Container{id}}}}}"
            ),
            "variables": {"id": screen_id, "sessionContext": SESSION_CTX, "limit": 10, "cursor": None},
        })
        contents = (((screen_data.get("data") or {}).get("screen") or {})
                    .get("screenContentsPage") or {}).get("screenContents") or []
        container_id = next((c["id"] for c in contents if c.get("__typename") == "Container" and c.get("id")), None)
        if not container_id:
            return []

        # Determine type filter based on slug prefix
        is_movies = slug.startswith("movies-")
        is_shows  = slug.startswith("shows-")

        # 3. GetGrid → paginate through all items
        GQL_GRID = (
            "query GetGrid($sessionContext:SessionContext!,$id:String!,$limit:Int,$cursor:String){"
            "container(sessionContext:$sessionContext id:$id){"
            "containerItemsPage(limit:$limit cursor:$cursor){"
            "cursor items{__typename ...on MediaMetadata{id title path mediaType shortDescription}}}}}"
        )
        shows = []
        cursor = None
        while True:
            grid_data = _gql({
                "query": GQL_GRID,
                "variables": {"id": container_id, "sessionContext": SESSION_CTX, "limit": 50, "cursor": cursor},
            })
            page = (((grid_data.get("data") or {}).get("container") or {})
                    .get("containerItemsPage") or {})
            for item in page.get("items") or []:
                if item.get("__typename") != "MediaMetadata":
                    continue
                media_type = (item.get("mediaType") or "").upper()
                if is_movies and media_type != "MOVIE":
                    continue
                if is_shows and media_type == "MOVIE":
                    continue
                path = item.get("path") or ""
                title = item.get("title") or ""
                if not path or not title:
                    continue
                shows.append({
                    "title":    title,
                    "synopsis": item.get("shortDescription") or "",
                    "url":      f"https://www.crave.ca/{path.lstrip('/')}",
                })
            cursor = page.get("cursor")
            if not cursor:
                break
        return shows

    def _cbc(self) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        cid = self._id
        BASE = "https://services.radio-canada.ca"
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

        def _make_url(item_url):
            if not item_url:
                return ""
            return item_url if item_url.startswith("http") else f"https://gem.cbc.ca/{item_url.lstrip('/')}"

        # cbc-genre:category/{slug}  or  cbc-collection:collection/{slug}
        # Both use content[0].items.results[] with pagination
        if cid.startswith("cbc-genre:") or cid.startswith("cbc-collection:"):
            slug = cid.split(":", 1)[1]
            # cbc-genre already includes type prefix (e.g. "category/drama")
            # cbc-collection slug is bare (e.g. "drama-films") — prepend "collection/"
            api_path = slug if cid.startswith("cbc-genre:") else f"collection/{slug}"
            shows = []
            seen = set()
            page = 1
            while True:
                qs = _up.urlencode({"device": "web", "pageSize": 60, "pageNumber": page})
                req = _ur.Request(f"{BASE}/ott/catalog/v2/gem/{api_path}?{qs}", headers=HDRS)
                with _ur.urlopen(req, timeout=30) as r:
                    data = _json.loads(r.read().decode())
                content = (data.get("content") or [{}])[0]
                items   = content.get("items") or {}
                for item in (items.get("results") or []):
                    title    = item.get("title") or ""
                    item_url = _make_url(item.get("url") or "")
                    if not title or not item_url or item_url in seen:
                        continue
                    seen.add(item_url)
                    shows.append({"title": title, "synopsis": item.get("description") or "", "url": item_url})
                if page >= (items.get("totalPages") or 1):
                    break
                page += 1
            return shows

        # cbc-section:{slug}  e.g. "cbc-section:kids"
        # Uses top-level lineups dict → paginated results → each result has items[] (flat list)
        if cid.startswith("cbc-section:"):
            section_slug = cid.split(":", 1)[1]
            shows = []
            seen = set()
            page = 1
            while True:
                qs = _up.urlencode({"device": "web", "pageSize": 20, "pageNumber": page})
                req = _ur.Request(f"{BASE}/ott/catalog/v2/gem/section/{section_slug}?{qs}", headers=HDRS)
                with _ur.urlopen(req, timeout=30) as r:
                    data = _json.loads(r.read().decode())
                lineups_page = data.get("lineups") or {}
                for lineup in (lineups_page.get("results") or []):
                    for item in (lineup.get("items") or []):
                        title    = item.get("title") or ""
                        item_url = _make_url(item.get("url") or "")
                        if not title or not item_url or item_url in seen:
                            continue
                        seen.add(item_url)
                        shows.append({"title": title, "synopsis": item.get("description") or "", "url": item_url})
                if page >= (lineups_page.get("totalPages") or 1):
                    break
                page += 1
            return shows

        return []

    def _threenow(self) -> list:
        import urllib.request as _ur, json as _json
        genre_slug = self._id.split(":", 1)[1] if self._id.startswith("threenow-genre:") else "all-shows"
        req = _ur.Request(
            "https://now-api.fullscreen.nz/v5/shows",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        shows = []
        for show in (data.get("shows") or []):
            show_id = show.get("showId") or ""
            title   = show.get("name") or ""
            if not show_id or not title:
                continue
            if genre_slug != "all-shows" and genre_slug not in (show.get("genres") or []):
                continue
            shows.append({
                "title":    title,
                "synopsis": "",
                "url":      f"https://www.threenow.co.nz/shows/{show_id}",
            })
        return sorted(shows, key=lambda s: s["title"])

    def _aubc(self) -> list:
        import urllib.request as _ur, json as _json
        slug = self._id.split(":", 1)[1] if self._id.startswith("aubc-cat:") else self._id
        BASE = "https://api.iview.abc.net.au"
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        req = _ur.Request(f"{BASE}/v3/category/{slug}", headers=HDRS)
        with _ur.urlopen(req, timeout=30) as r:
            data = _json.loads(r.read().decode())
        shows = []
        seen = set()
        for coll in ((data.get("_embedded") or {}).get("collections") or []):
            for item in (coll.get("items") or []):
                title    = item.get("title") or ""
                item_url = item.get("shareUrl") or ""
                if not title or not item_url or item_url in seen:
                    continue
                seen.add(item_url)
                shows.append({
                    "title":    title,
                    "synopsis": item.get("shortSynopsis") or "",
                    "url":      item_url,
                })
        return sorted(shows, key=lambda s: s["title"])

    def _seven(self) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "x-swm-apikey": "kGcrNnuPClrkynfnKwG8IA/NhVG6ut5nPEdWF2jscvE="}
        mreq = _ur.Request("https://market-cdn.swm.digital/v1/market/ip?apikey=web", headers=HDRS)
        try:
            with _ur.urlopen(mreq, timeout=8) as r:
                market_id = _json.loads(r.read().decode()).get("_id", 4)
        except Exception:
            market_id = 4
        base_params = _up.urlencode({
            "platform-id": "web", "market-id": market_id,
            "platform-version": "5.25.0.0", "api-version": "5.9.0.0",
        })

        def _fetch_shelf(page_slug: str, component_id: str) -> list:
            req = _ur.Request(
                f"https://component.swm.digital/v2/component/{page_slug}"
                f"?component-id={component_id}&{base_params}", headers=HDRS)
            try:
                with _ur.urlopen(req, timeout=15) as r:
                    shelf_data = _json.loads(r.read().decode())
            except Exception:
                return []
            out = []
            for mi in (shelf_data.get("mediaItems") or []):
                title  = mi.get("title") or ""
                cl_url = (mi.get("contentLink") or {}).get("url") or ""
                if title and cl_url:
                    out.append((title, mi.get("description") or "", cl_url))
            return out

        shows = []
        seen = set()

        if self._id.startswith("seven-genre:"):
            # Build movie URL exclusion set from ALL movies page shelves
            movie_urls: set = set()
            try:
                mv_page_req = _ur.Request(
                    f"https://component-cdn.swm.digital/content/movies?{base_params}", headers=HDRS)
                with _ur.urlopen(mv_page_req, timeout=15) as r:
                    mv_page = _json.loads(r.read().decode())
                mv_shelf_ids = [
                    str(item.get("id")) for item in (mv_page.get("items") or [])
                    if isinstance(item, dict) and item.get("type") == "mediaShelf" and item.get("id")
                ]
                for mv_sid in mv_shelf_ids:
                    for _, _, cl_url in _fetch_shelf("movies", mv_sid):
                        movie_urls.add(cl_url)
            except Exception:
                pass  # If movies fetch fails, carry on without exclusion

            # TV Shows genre page — aggregate across shelves, excluding movies
            genre_slug = self._id.split(":", 1)[1]
            page_req = _ur.Request(
                f"https://component-cdn.swm.digital/content/{genre_slug}?{base_params}",
                headers=HDRS)
            with _ur.urlopen(page_req, timeout=20) as r:
                page = _json.loads(r.read().decode())
            MOVIE_SHELF_WORDS = {"movie", "blockbuster", "film"}
            shelf_ids = [
                (str(item.get("id")), item.get("title") or "")
                for item in (page.get("items") or [])
                if isinstance(item, dict) and item.get("type") == "mediaShelf" and item.get("id")
            ]
            # Skip shelves whose titles are clearly movie-focused
            tv_shelf_ids = [
                sid for sid, stitle in shelf_ids
                if not any(w in stitle.lower() for w in MOVIE_SHELF_WORDS)
            ]
            for shelf_id in tv_shelf_ids[:6]:
                for title, synopsis, cl_url in _fetch_shelf(genre_slug, shelf_id):
                    if cl_url not in seen and cl_url not in movie_urls:
                        seen.add(cl_url)
                        shows.append({"title": title, "synopsis": synopsis,
                                      "url": f"https://7plus.com.au{cl_url}"})

        elif self._id.startswith("seven-shelf:"):
            # Movies genre shelf — single direct component fetch
            parts = self._id.split(":")
            page_slug    = parts[1] if len(parts) > 1 else "movies"
            component_id = parts[2] if len(parts) > 2 else ""
            if component_id:
                for title, synopsis, cl_url in _fetch_shelf(page_slug, component_id):
                    if cl_url not in seen:
                        seen.add(cl_url)
                        shows.append({"title": title, "synopsis": synopsis,
                                      "url": f"https://7plus.com.au{cl_url}"})

        return sorted(shows, key=lambda s: s["title"])

    def _ten(self) -> list:
        import urllib.request as _ur, urllib.parse as _up, json as _json
        genre_slug = self._id.split(":", 1)[1] if ":" in self._id else self._id
        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"}
        req = _ur.Request(f"https://10.com.au/shows/{genre_slug}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
        data = SearchWorker._ten_brace_extract(html, 'const showsPageData = ')
        if not data:
            return []
        shows = []
        seen_urls = set()
        loaded_ids = []
        for show in (data.get("shows") or []):
            name = show.get("name") or show.get("title") or ""
            show_url = show.get("url") or ""
            if name and show_url and show_url not in seen_urls:
                seen_urls.add(show_url)
                shows.append({"title": name, "synopsis": show.get("abstractShowDescription") or "", "url": show_url})
                if show.get("id"):
                    loaded_ids.append(str(show["id"]))
        genre_id = (data.get("selectedGenre") or {}).get("id")
        sort = data.get("sort") or "title"
        sort_dir = data.get("sortDirection") or "asc"
        if data.get("hasMore") and genre_id:
            API_HDRS = {"User-Agent": UA, "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"https://10.com.au/shows/{genre_slug}"}
            for _ in range(50):
                qs = _up.urlencode({
                    "skipIdList": ",".join(loaded_ids),
                    "genreId": genre_id,
                    "sort": sort,
                    "sortDirection": sort_dir,
                })
                try:
                    req2 = _ur.Request(f"https://10.com.au/api/shows?{qs}", headers=API_HDRS)
                    with _ur.urlopen(req2, timeout=15) as r2:
                        page = _json.loads(r2.read().decode("utf-8", errors="replace"))
                except Exception:
                    break
                page_shows = page.get("items") or []
                if not page_shows:
                    break
                for show in page_shows:
                    name = show.get("name") or show.get("title") or ""
                    show_url = show.get("url") or ""
                    if name and show_url and show_url not in seen_urls:
                        seen_urls.add(show_url)
                        shows.append({"title": name, "synopsis": show.get("abstractShowDescription") or "", "url": show_url})
                    if show.get("id"):
                        loaded_ids.append(str(show["id"]))
                if not page.get("hasMore"):
                    break
        return sorted(shows, key=lambda s: s["title"])

    def _rte(self) -> list:
        import urllib.parse as _up, re as _re
        tag = self._id  # category title e.g. "Drama", "Film"
        BASE = "https://feed.entertainment.tv.theplatform.eu/f/1uC-gC/rte-prd-prd-search"
        shows = []
        seen = set()
        start = 1
        page_size = 50
        while True:
            qs = _up.urlencode({
                "byProgramType": "Series|Movie",
                "byTags": tag,
                "range": f"{start}-{start + page_size - 1}",
                "schema": "2.15",
                "omitInvalidFields": "true",
            })
            data = self._fetch_json(f"{BASE}?{qs}")
            entries = data.get("entries") or []
            if not entries:
                break
            for e in entries:
                title = e.get("title") or e.get("plprogram$longTitle") or ""
                guid  = e.get("guid") or ""
                prog_type = e.get("plprogram$programType") or "Series"
                synopsis  = e.get("plprogram$shortDescription") or e.get("description") or ""
                if not title or not guid or guid in seen:
                    continue
                seen.add(guid)
                slug = _re.sub(r"^-|-$", "", _re.sub(r"\W+", "-", title.lower()))
                url  = f"https://www.rte.ie/player/{prog_type}/{slug}/{guid}"
                shows.append({"title": title, "synopsis": synopsis, "url": url,
                               "_rte_guid": guid, "_rte_type": prog_type})
            if len(entries) < page_size:
                break
            start += page_size
        return sorted(shows, key=lambda s: s["title"])

    def _sbs(self) -> list:
        import urllib.request as _ur, json as _json
        cat_id = self._id
        HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json", "Origin": "https://www.sbs.com.au"}

        if cat_id.startswith("sbs-col:") or cat_id.startswith("sbs-movie:"):
            is_movie = cat_id.startswith("sbs-movie:")
            col_slug = cat_id.split(":", 1)[1]
            is_news = col_slug == "news-and-current-affairs"
            if is_movie:
                ok_types = {"MOVIE"}
            elif is_news:
                ok_types = {"TV_SERIES", "NEWS_SERIES"}
            else:
                ok_types = {"TV_SERIES"}
            out = []
            cursor = ""
            for _ in range(20):
                qs = f"limit=100{'&cursor=' + cursor if cursor else ''}"
                req = _ur.Request(f"https://catalogue.pr.sbsod.com/collections/{col_slug}?{qs}", headers=HDRS)
                with _ur.urlopen(req, timeout=20) as r:
                    data = _json.loads(r.read().decode())
                for item in (data.get("items") or []):
                    etype = item.get("entityType") or ""
                    if etype not in ok_types:
                        continue
                    slug = item.get("slug") or ""
                    title = item.get("title") or ""
                    if not slug or not title:
                        continue
                    if is_movie:
                        url = f"https://www.sbs.com.au/ondemand/movie/{slug}"
                    elif etype == "NEWS_SERIES":
                        url = f"https://www.sbs.com.au/ondemand/news-series/{slug}"
                    else:
                        url = f"https://www.sbs.com.au/ondemand/tv-series/{slug}"
                    out.append({"title": title, "synopsis": item.get("description") or "", "url": url})
                cursor = (data.get("meta") or {}).get("nextCursor") or ""
                if not cursor:
                    break
            return sorted(out, key=lambda s: s["title"])

        elif cat_id.startswith("sbs-page:"):
            page_slug = cat_id.split(":", 1)[1]
            req = _ur.Request(f"https://catalogue.pr.sbsod.com/pages/{page_slug}?limit=10", headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                data = _json.loads(r.read().decode())
            seen = set()
            out = []
            for sec in (data.get("sections") or []):
                if sec.get("displayType") == "SHORTCUT_BAR":
                    continue
                for item in (sec.get("items") or []):
                    if item.get("entityType") != "TV_SERIES":
                        continue
                    slug = item.get("slug") or ""
                    title = item.get("title") or ""
                    if not slug or not title or slug in seen:
                        continue
                    seen.add(slug)
                    out.append({"title": title,
                                "synopsis": item.get("description") or "",
                                "url": f"https://www.sbs.com.au/ondemand/tv-series/{slug}"})
            return sorted(out, key=lambda s: s["title"])

        return []


class BrowseWorker(QThread):
    """Fetches episode list for a show URL via service APIs."""
    status = pyqtSignal(str)
    done   = pyqtSignal(list)   # list of {title, url, synopsis, series_no}
    error  = pyqtSignal(str)

    def __init__(self, service_id: str, url: str):
        super().__init__()
        self._service = service_id
        self._url     = url

    def _fetch(self, url: str, headers: dict | None = None) -> bytes:
        import urllib.request
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/html, */*",
            "Accept-Encoding": "gzip",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as resp:
            import gzip as _gz
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            return raw

    def _fetch_json(self, url: str, headers: dict | None = None) -> dict | list:
        return json.loads(self._fetch(url, headers).decode("utf-8"))

    def run(self):
        svc = self._service
        url = self._url
        try:
            if svc == "iP":
                episodes = self._bbc(url)
            elif svc == "ALL4":
                episodes = self._all4(url)
            elif svc == "ITV":
                episodes = self._itvx(url)
            elif svc == "MY5":
                episodes = self._my5(url)
            elif svc == "UKTV":
                episodes = self._uktv(url)
            elif svc == "STV":
                episodes = self._stv(url)
            elif svc == "RTE":
                episodes = self._rte(url)
            elif svc == "PLUTO":
                episodes = self._pluto(url)
            elif svc == "TUBI":
                episodes = self._tubi(url)
            elif svc == "TVNZ":
                episodes = self._tvnz(url)
            elif svc == "NINE":
                episodes = self._nine(url)
            elif svc == "NBC":
                episodes = self._nbc(url)
            elif svc == "NRK":
                episodes = self._nrk(url)
            elif svc == "ARD":
                episodes = self._ard(url)
            elif svc == "ZDF":
                episodes = self._zdf(url)
            elif svc == "RKTN":
                episodes = self._rktn(url)
            elif svc == "VM":
                episodes = self._vm(url)
            elif svc == "ROKU":
                episodes = self._roku(url)
            elif svc == "CBS":
                episodes = self._cbs(url)
            elif svc == "CWTV":
                episodes = self._cwtv(url)
            elif svc == "PBS":
                episodes = self._pbs(url)
            elif svc == "CRAV":
                episodes = self._crav(url)
            elif svc == "CBC":
                episodes = self._cbc(url)
            elif svc == "ThreeNow":
                episodes = self._threenow(url)
            elif svc == "AUBC":
                episodes = self._aubc(url)
            elif svc == "SEVEN":
                episodes = self._seven(url)
            elif svc == "TEN":
                episodes = self._ten(url)
            elif svc == "SBS":
                episodes = self._sbs(url)
            else:
                self.error.emit(
                    f"Episode listing not yet available for {svc}.\n"
                    f"Paste a direct episode URL and use Download instead."
                )
                return
            if not episodes:
                self.error.emit("No episodes found for this title.")
            else:
                self.done.emit(episodes)
        except Exception as exc:
            self.error.emit(f"Failed to fetch episode list: {exc}")

    def _bbc(self, brand_url: str) -> list:
        pid = brand_url.rstrip("/").split("/")[-1]
        api_key = "D2FgtcTxGqqIgLsfBWTJdrQh2tVdeaAp"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko",
            "Origin": "https://www.bbc.com",
            "Referer": "https://www.bbc.com/",
        }
        self.status.emit(f"BBC: fetching episodes for {pid}...")
        url = (f"https://ibl.api.bbci.co.uk/ibl/v1/programmes/{pid}/episodes"
               f"?rights=mobile&availability=available&page=1&per_page=200&api_key={api_key}")
        data = self._fetch_json(url, hdrs)
        episodes = []
        elements = (data.get("programme_episodes") or {}).get("elements") or []
        for item in elements:
            ep_id = item.get("id", "")
            subtitle = item.get("subtitle", "")
            synopsis = (item.get("synopses") or {}).get("small", "")
            try:
                series_no = subtitle.split(":")[0].split(" ")[1]
                int(series_no)
            except Exception:
                series_no = "0"
            # subtitle is e.g. "Series 3: Episode 1" — strip the "Series X: " prefix
            # since the season grouping already shows that context
            display_title = subtitle
            if ":" in subtitle:
                display_title = subtitle.split(":", 1)[1].strip()
            episodes.append({
                "series_no": series_no,
                "title":     display_title or subtitle or ep_id,
                "url":       f"https://www.bbc.co.uk/iplayer/episode/{ep_id}",
                "synopsis":  synopsis or "",
            })
        return episodes

    def _all4(self, brand_url: str) -> list:
        self.status.emit("ALL4: fetching episode list...")
        html = self._fetch(brand_url).decode("utf-8", errors="replace")
        import re as _re
        # Flatten page (matching envied's get_html approach)
        flat = html.replace("‌", "").replace("\r\n", "").replace("undefined", "null")
        m = _re.search(r"<script>window\.__PARAMS__ = ", flat)
        if not m:
            raise ValueError("Could not find __PARAMS__ in ALL4 page.")
        # Use raw_decode to extract exactly one JSON object starting at the match end
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(flat, m.end())
        brand = (data.get("initialData") or {}).get("brand") or {}
        eps_raw = brand.get("episodes") or []
        episodes = []
        for item in eps_raw:
            href = item.get("hrefLink") or ""
            episodes.append({
                "series_no": str(item.get("seriesNumber", "0")),
                "title":     item.get("title", "Unknown"),
                "url":       f"https://www.channel4.com{href}" if href else "",
                "synopsis":  item.get("summary", ""),
            })
        return [e for e in episodes if e["url"]]

    def _itvx(self, show_url: str) -> list:
        self.status.emit(f"ITVX: fetching {show_url}")
        # Dalvik UA — ITV serves the page fine to this UA
        html = self._fetch(show_url, headers={
            "Accept": "*/*",
            "user-agent": "Dalvik/2.9.8 (Linux; U; Android 9.9.2; ALE-L94 Build/NJHGGF)",
            "Origin": "https://www.itv.com",
            "Referer": "https://www.itv.com/",
        }).decode("utf-8", errors="replace")
        import re as _re
        m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>', html, _re.DOTALL)
        if not m:
            raise ValueError("Could not find __NEXT_DATA__ in ITVX page.")
        data = json.loads(m.group(1))

        # Pull programmeSlug and programmeId from query section
        query = data.get("query", {})
        programme_slug = query.get("programmeSlug", "")
        programme_id   = query.get("programmeId", "")

        props = data.get("props", {}).get("pageProps", {})
        episodes = []
        for series in (props.get("seriesList") or []):
            s_no_raw = series.get("seriesNumber") or series.get("series")
            try:
                s_no = str(int(s_no_raw)) if s_no_raw is not None else "100"
            except (ValueError, TypeError):
                s_no = "100"
            for title_item in (series.get("titles") or []):
                # Use encodedEpisodeId.letterA for the episode URL fragment
                letter_a = (title_item.get("encodedEpisodeId") or {}).get("letterA", "")
                ep_title = title_item.get("episodeTitle") or title_item.get("title") or ""
                ep_no_raw = title_item.get("episode") or title_item.get("episodeNumber")
                try:
                    ep_no = str(int(ep_no_raw)) if ep_no_raw is not None else ""
                except (ValueError, TypeError):
                    ep_no = ""
                ep_url = (
                    f"https://www.itv.com/watch/{programme_slug}/{programme_id}/{letter_a}"
                    if letter_a else ""
                )
                episodes.append({
                    "series_no": s_no,
                    "ep_no":     ep_no,
                    "title":     ep_title,
                    "url":       ep_url,
                    "synopsis":  title_item.get("description", "") or title_item.get("synopsis", ""),
                })
        return [e for e in episodes if e["url"]]

    def _my5(self, show_url: str) -> list:
        self.status.emit("My5: fetching episode list...")
        # Extract show slug — URL may be the seasons API URL or a channel5.com show URL
        import urllib.parse as _up
        path = _up.urlparse(show_url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if "shows" in parts:
            f_name = parts[parts.index("shows") + 1]
        elif "show" in parts:
            f_name = parts[parts.index("show") + 1]
        else:
            f_name = parts[-1] if parts else ""
        # If the URL already is the seasons API URL, use it directly; else build it
        if "seasons.json" in show_url:
            seasons_url = show_url
        else:
            seasons_url = (
                f"https://corona.channel5.com/shows/{f_name}/seasons.json"
                f"?platform=my5desktop&friendly=1"
            )
        data = self._fetch_json(seasons_url)
        episodes = []
        for season in (data.get("seasons") or []):
            s_no_raw = season.get("seasonNumber")
            if s_no_raw is None:
                # Single episode / film — download the show URL directly
                episodes.append({
                    "series_no": "0",
                    "title":     season.get("title") or f_name,
                    "url":       f"https://www.channel5.com/show/{f_name}",
                    "synopsis":  "",
                })
                continue
            s_no = str(s_no_raw)
            eps_url = (
                f"https://corona.channel5.com/shows/{f_name}/seasons/{s_no_raw}/episodes.json"
                f"?platform=my5desktop&friendly=1&linear=true"
            )
            try:
                ep_data = self._fetch_json(eps_url)
                for ep in (ep_data.get("episodes") or []):
                    sh = ep.get("sh_f_name") or f_name
                    sea = ep.get("sea_f_name") or f"season-{s_no}"
                    ep_f = ep.get("f_name") or ""
                    ep_url = f"https://www.channel5.com/{sh}/{sea}/{ep_f}" if ep_f else ""
                    if not ep_url:
                        continue
                    ep_num = ep.get("ep_num") or ""
                    title = f"{ep_num}:{ep.get('title', '')}" if ep_num else ep.get("title", "Unknown")
                    episodes.append({
                        "series_no": str(ep.get("sea_num") or s_no),
                        "title":     title,
                        "url":       ep_url,
                        "synopsis":  ep.get("s_desc") or "",
                    })
            except Exception:
                continue
        return episodes

    def _uktv(self, brand_url: str) -> list:
        self.status.emit("UKTV: fetching episode list...")
        # Extract slug — URL may be https://u.co.uk/shows/{slug}/watch-online
        if "vschedules.uktv.co.uk" not in brand_url:
            parts = brand_url.rstrip("/").split("/")
            try:
                slug = parts[parts.index("shows") + 1]
            except (ValueError, IndexError):
                slug = parts[-1]
            brand_url = f"https://vschedules.uktv.co.uk/vod/brand/?slug={slug}"
        hdrs = {"user-agent": "okhttp/4.7.2"}
        data = self._fetch_json(brand_url, hdrs)
        # UKTV brand API: get series IDs, then fetch each series for episodes
        series_ids = [s["id"] for s in (data.get("series") or []) if s.get("id")]
        episodes = []
        brand_slug = ""
        for sid in series_ids:
            try:
                s_data = self._fetch_json(
                    f"https://vschedules.uktv.co.uk/vod/series/?id={sid}", hdrs)
                for ep in (s_data.get("episodes") or []):
                    ep_number  = ep.get("episode_number", "1")
                    ser_number = ep.get("series_number", "0")
                    video_id   = ep.get("video_id", "")
                    brand_slug = ep.get("brand_slug", brand_slug)
                    ep_url = (f"https://u.co.uk/shows/{brand_slug}"
                              f"/series-{ser_number}/episode-{ep_number}/{video_id}")
                    episodes.append({
                        "series_no": str(ser_number),
                        "ep_no":     str(ep_number),
                        "title":     ep.get("name") or str(ep_number),
                        "url":       ep_url,
                        "synopsis":  ep.get("synopsis", ""),
                    })
            except Exception:
                continue
        return episodes

    def _stv(self, show_url: str) -> list:
        # Fetch summary page, parse __NEXT_DATA__ for series tabs
        self.status.emit("STV: fetching episode list...")
        import re as _re, json as _json
        hdrs = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "Origin": "https://player.stv.tv",
            "Referer": "https://player.stv.tv/",
        }
        html = self._fetch(show_url, hdrs).decode("utf-8", errors="replace")
        m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>', html, _re.DOTALL)
        if not m:
            raise ValueError("Could not find __NEXT_DATA__ in STV page.")
        data = _json.loads(m.group(1))
        page_data = (data.get("props") or {}).get("pageProps", {}).get("data") or {}
        tabs = page_data.get("tabs") or []
        episodes = []

        def _series_no_from_tab_title(title: str) -> str:
            # "Series 3" → "3", "Episodes" → "0"
            try:
                if "Series" in title:
                    return str(int(title.split()[-1]))
            except Exception:
                pass
            return "0"

        # Tab 0 — first series/episodes embedded directly in the page
        if tabs:
            tab0 = tabs[0]
            s_no = _series_no_from_tab_title(tab0.get("title", ""))
            for item in (tab0.get("data") or []):
                if not isinstance(item, dict):
                    continue
                link = item.get("link", "")
                title = item.get("title", "")
                if not link:
                    continue
                url = f"https://player.stv.tv{link}" if not link.startswith("http") else link
                episodes.append({
                    "series_no": s_no,
                    "title":     title,
                    "url":       url,
                    "synopsis":  item.get("summary", ""),
                })

        # Additional tabs — fetch via series GUID API
        api_hdrs = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
            "Accept": "*/*",
            "Referer": "https://player.stv.tv/",
            "Stv-Drm": "true",
            "Origin": "https://player.stv.tv",
        }
        for tab in tabs[1:]:
            if not isinstance(tab, dict):
                continue
            tab_title = tab.get("title", "")
            if "Autoplay" in tab_title or "Trailer" in tab_title:
                break
            try:
                series_guid = tab["params"]["query"]["series.guid"]
            except (KeyError, TypeError):
                continue
            try:
                api_data = self._fetch_json(
                    f"https://player.api.stv.tv/v1/episodes?series.guid={series_guid}&limit=100&groupToken=0071",
                    api_hdrs)
                for item in (api_data.get("results") or []):
                    if not isinstance(item, dict):
                        continue
                    s_name = (item.get("playerSeries") or {}).get("name", "")
                    try:
                        s_no = str(int(s_name.replace("Series ", "")))
                    except Exception:
                        s_no = "0"
                    url = item.get("_permalink", "")
                    if not url:
                        continue
                    episodes.append({
                        "series_no": s_no,
                        "title":     item.get("title", ""),
                        "url":       url,
                        "synopsis":  item.get("summary", ""),
                    })
            except Exception:
                continue
        return episodes

    def _rte(self, show_url: str) -> list:
        # RTE: guid → series ID → episodes
        # URL format: https://www.rte.ie/player/{type}/{title_slug}/{guid}
        self.status.emit("RTE: fetching episode list...")
        import re as _re, unicodedata as _ud
        parts = show_url.rstrip("/").split("/")
        guid  = parts[-1]
        prog_type = parts[-3].lower() if len(parts) >= 3 else "series"

        # Non-series (Movie/Clip): return as single downloadable item
        if prog_type != "series":
            title = parts[-2].replace("-", " ").title()
            return [{"series_no": "0", "title": title, "url": show_url, "synopsis": ""}]

        # Step 1: resolve series GUID → internal series ID
        data = self._fetch_json(
            f"https://www.rte.ie/mpx/1uC-gC/rte-prd-prd-all-movies-series?byGuid={guid}"
        )
        entries = data.get("entries") or []
        if not entries:
            raise ValueError(f"RTE: no series found for GUID {guid}")
        serid = entries[0].get("id", "").split("/")[-1]
        if not serid:
            raise ValueError("RTE: could not extract series ID")

        # Step 2: fetch all episodes for the series
        ep_data = self._fetch_json(
            f"https://www.rte.ie/mpx/1uC-gC/rte-prd-prd-all-programs?bySeriesId={serid}"
        )
        episodes = []
        for result in (ep_data.get("entries") or []):
            if not isinstance(result, dict):
                continue
            ep_guid  = result.get("guid", "")
            ep_title = result.get("title") if result.get("plprogram$programType", "").lower() == "series" \
                       else result.get("plprogram$longTitle", "")
            season_no  = str(result.get("plprogram$tvSeasonNumber") or "0")
            episode_no = str(result.get("plprogram$tvSeasonEpisodeNumber") or "0")
            synopsis   = result.get("plprogram$shortDescription", "")
            if not ep_guid or not ep_title:
                continue
            # Use plprogram$seriesId from the episode itself for URL building
            ep_serid = (result.get("plprogram$seriesId") or "").split("/")[-1] or serid
            # Build slug from episode title
            slug = _re.sub(r"^-|-$", "", _re.sub(r"\W+", "-", ep_title.lower()))
            # Normalize away Irish accented characters
            slug = _ud.normalize("NFKD", slug).encode("ASCII", "ignore").decode()
            ep_url = f"https://www.rte.ie/player/series/{slug}/{ep_serid}?epguid={ep_guid}"
            episodes.append({
                "series_no": season_no,
                "title":     f"{episode_no} {ep_title.replace(',', '')}",
                "url":       ep_url,
                "synopsis":  synopsis,
            })
        return episodes

    def _pluto(self, url: str) -> list:
        import re as _re, uuid as _uuid, urllib.parse as _up
        # Extract series ID from URL (series only — movies go direct)
        m = _re.search(r'/series/([a-zA-Z0-9]+)', url)
        if not m:
            return []
        series_id = m.group(1)
        self.status.emit(f"Pluto TV: fetching season list for {series_id}…")
        params = _up.urlencode({
            "appName": "web", "appVersion": "na",
            "clientID": str(_uuid.uuid1()), "deviceDNT": 0,
            "deviceId": "unknown", "clientModelNumber": "na",
            "deviceMake": "unknown", "deviceModel": "web",
            "deviceType": "web", "deviceVersion": "unknown",
        })
        data = self._fetch_json(
            f"https://service-vod.clusters.pluto.tv/v3/vod/series/{series_id}/seasons?{params}"
        )
        episodes = []
        for season in (data.get("seasons") or []):
            season_no = str(season.get("number", 0))
            for ep in (season.get("episodes") or []):
                ep_id   = ep.get("_id", "")
                ep_num  = ep.get("number", 0)
                ep_name = ep.get("name", f"Episode {ep_num}")
                ep_url  = (f"https://pluto.tv/on-demand/series/{series_id}"
                           f"/season/{season_no}/episode/{ep_id}")
                episodes.append({
                    "series_no": season_no,
                    "title":     f"Episode {ep_num}: {ep_name}" if ep_name else f"Episode {ep_num}",
                    "url":       ep_url,
                    "synopsis":  ep.get("description", ""),
                })
        return episodes

    def _tubi(self, url: str) -> list:
        import re as _re, uuid as _uuid, urllib.parse as _up
        # Extract content ID and type from URL
        m = _re.search(r'/(series|movies|tv-shows)/([a-z0-9-]+)', url)
        if not m:
            return []
        kind, content_id = m.group(1), m.group(2)
        if kind == "movies":
            return []  # Movies go direct
        self.status.emit(f"Tubi: fetching series {content_id}…")
        params = _up.urlencode({
            "app_id": "tubitv",
            "platform": "web",
            "device_id": str(_uuid.uuid4()),
            "content_id": content_id,
        })
        data = self._fetch_json(
            f"https://uapi.adrise.tv/cms/content?{params}"
        )
        episodes = []
        for season in (data.get("children") or []):
            season_no = str(season.get("id", 0))
            for ep in (season.get("children") or []):
                ep_id    = ep.get("id", "")
                ep_num   = ep.get("episode_number", 0)
                ep_title = ep.get("title", "")
                # Strip "Show Name - Episode Title" format if present
                if " - " in ep_title:
                    ep_title = ep_title.split(" - ", 1)[1]
                slug = (ep_title.lower().replace(" ", "-").replace(":", "")
                        .replace("(", "").replace(")", "")
                        .replace(".", "").replace("'", "-"))
                ep_url = f"https://tubitv.com/tv-shows/{ep_id}/{slug}"
                episodes.append({
                    "series_no": season_no,
                    "title":     f"Episode {ep_num}: {ep_title}" if ep_title else f"Episode {ep_num}",
                    "url":       ep_url,
                    "synopsis":  ep.get("description", ""),
                })
        return episodes

    def _nine(self, show_url: str) -> list:
        import re as _re, urllib.parse as _up
        # Extract series slug from URL e.g. https://www.9now.com.au/travel-guides
        m = _re.search(r'9now\.com\.au/([a-z0-9-]+)', show_url)
        if not m:
            return []
        series_slug = m.group(1)
        self.status.emit(f"9Now: fetching series {series_slug}…")
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.9now.com.au",
            "Referer": "https://www.9now.com.au/",
        }
        series_data = self._fetch_json(
            f"https://tv-api.9now.com.au/v2/pages/tv-series/{series_slug}?device=web",
            headers=hdrs,
        )
        seasons = [s.get("slug") for s in series_data.get("seasons", []) if s.get("slug")]
        episodes = []
        for season_slug in seasons:
            ep_data = self._fetch_json(
                f"https://tv-api.9now.com.au/v2/pages/tv-series/{series_slug}/seasons/{season_slug}/episodes/?device=web",
                headers=hdrs,
            )
            for ep in ep_data.get("episodes", {}).get("items", []):
                video = ep.get("video") or {}
                if not video.get("brightcoveId"):
                    continue
                ep_num = ep.get("episodeNumber", 0)
                link = (ep.get("link") or {}).get("webUrl", "")
                ep_url = f"https://www.9now.com.au{link}" if link.startswith("/") else link
                season_no = season_slug
                m_sn = _re.search(r"season-(\d+)", season_slug)
                if m_sn:
                    season_no = m_sn.group(1)
                episodes.append({
                    "series_no": season_no,
                    "title":     f"Episode {ep_num}: {ep.get('name') or ep.get('displayName', '')}",
                    "url":       ep_url,
                    "synopsis":  ep.get("description", ""),
                })
        return episodes

    def _nbc(self, show_url: str) -> list:
        import re as _re, json as _json, urllib.request as _req, urllib.parse as _up, gzip as _gz

        m = _re.search(r'nbc\.com/([a-z0-9][a-z0-9-]+)', show_url)
        slug = m.group(1) if m else None

        # Step 1: fetch PRELOAD from the show HTML page
        show_title = None
        sections   = []
        self.status.emit("NBC: fetching show page…")
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.nbc.com",
            "Referer": "https://www.nbc.com/",
        }
        try:
            req = _req.Request(show_url, headers=hdrs)
            with _req.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = _gz.decompress(raw)
                html = raw.decode("utf-8", errors="replace")
            marker = "PRELOAD="
            start = html.find(marker + "{")
            if start >= 0:
                start += len(marker)
                end = html.find("</script>", start)
                preload = _json.loads(html[start:end].strip().rstrip(";"))
                page = next(iter((preload.get("pages") or {}).values()), None)
                if page:
                    base = page.get("base") or {}
                    sections  = (base.get("data") or {}).get("sections") or []
                    show_title = (base.get("metadata") or {}).get("shortTitle")
        except Exception:
            pass

        # Step 2: parse episodes from PRELOAD sections
        episodes = []
        for section in sections:
            if section.get("component") != "LinksSelectableGroup":
                continue
            if (section.get("data") or {}).get("optionalTitle") != "Episodes":
                continue
            for shelf in (section.get("data") or {}).get("items") or []:
                for tile in (shelf.get("data") or {}).get("items") or []:
                    tile_data = tile.get("data") or {}
                    if tile_data.get("programmingType") != "Full Episode":
                        continue
                    permalink = (tile_data.get("permalink") or "").replace("http://", "https://")
                    if not permalink:
                        continue
                    season_n  = tile_data.get("seasonNumber", 0)
                    episode_n = tile_data.get("episodeNumber", 0)
                    name      = tile_data.get("secondaryTitle") or tile_data.get("title") or ""
                    episodes.append({
                        "series_no": str(season_n),
                        "title":     f"S{int(season_n):02d}E{int(episode_n):02d}: {name}" if season_n else name,
                        "url":       permalink,
                        "synopsis":  tile_data.get("description", ""),
                    })

        # Step 3: if PRELOAD had no episodes, fall back to Algolia episode index
        if not episodes and slug:
            self.status.emit("NBC: fetching episode list via Algolia…")
            algolia_url  = "https://3nkvntt7f3-dsn.algolia.net/1/indexes/*/queries"
            app_id       = "3NKVNTT7F3"
            api_key      = "c2df90d0ff616a2726139c671d6e6e8e"
            index        = "prod_multi-brand-unified-web"
            algolia_hdrs = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "x-algolia-api-key": api_key,
                "x-algolia-application-id": app_id,
                "Origin": "https://www.nbc.com",
            }

            def _algolia_ep_fetch(query="", facet_filter=None):
                hits = []
                facets = ["algoliaProperties.entityType:episodes"]
                if facet_filter:
                    facets.append(facet_filter)
                page_num = 0
                while True:
                    params = _up.urlencode({
                        "query": query,
                        "facetFilters": _json.dumps(facets),
                        "hitsPerPage": 100,
                        "page": page_num,
                    })
                    body = _json.dumps({"requests": [{"indexName": index, "params": params}]}).encode()
                    req = _req.Request(algolia_url, data=body, headers=algolia_hdrs, method="POST")
                    with _req.urlopen(req, timeout=20) as resp:
                        result = _json.loads(resp.read().decode("utf-8"))
                    result0 = result["results"][0]
                    hits.extend(result0.get("hits") or [])
                    if page_num + 1 >= result0.get("nbPages", 1):
                        break
                    page_num += 1
                return hits

            try:
                algolia_hits = _algolia_ep_fetch(facet_filter=f"series.urlAlias:{slug}")
                if not algolia_hits and show_title:
                    algolia_hits = _algolia_ep_fetch(query=show_title)
                for h in algolia_hits:
                    ep_data   = h.get("episegment") or {}
                    video     = h.get("video") or {}
                    season    = h.get("season") or {}
                    prog_type = ep_data.get("programmingType")
                    if prog_type is not None:
                        pt = prog_type if isinstance(prog_type, str) else (prog_type[0] if prog_type else "")
                        if pt != "Full Episode":
                            continue
                    permalink = (video.get("permalink") or "").replace("http://", "https://")
                    if not permalink:
                        continue
                    season_n  = season.get("seasonNumber", 0)
                    episode_n = ep_data.get("episodeNumber", 0)
                    name      = ep_data.get("title") or ""
                    episodes.append({
                        "series_no": str(season_n),
                        "title":     f"S{int(season_n):02d}E{int(episode_n):02d}: {name}" if season_n else name,
                        "url":       permalink,
                        "synopsis":  ep_data.get("shortDescription", ""),
                    })
            except Exception as e:
                self.status.emit(f"NBC: Algolia episode fetch failed: {e}")

        return episodes

    def _tvnz(self, show_url: str) -> list:
        import re as _re, urllib.parse as _up
        # Normalise URL: strip www
        show_url = show_url.replace("https://www.tvnz.co.nz/", "https://tvnz.co.nz/")

        # Parse content type from URL path
        m = _re.search(
            r'tvnz\.co\.nz/(?:player/)?(tvseries|tvepisode|movie|event|sporthighlight|newsclip|sportclip)/([^/?#]+)',
            show_url,
        )
        if not m:
            return []
        cty, slug = m.group(1), m.group(2)

        # Non-series content has no episode list — return single item for direct download
        if cty != "tvseries":
            self.status.emit(f"TVNZ: single {cty} — going straight to download")
            return [{
                "series_no": 1,
                "title":     slug.replace("-", " ").title(),
                "url":       show_url,
                "synopsis":  "",
            }]

        _base_headers = {
            "x-device-type":    "androidtv",
            "x-app-store-type": "androidtv",
            "x-client-id":      "tvnz-tvnz-androidtv",
            "User-Agent":       "Dalvik/2.1.0 (Linux; U; Android 11; Android TV Build/RTMA.250416.082)",
        }
        _params = _up.urlencode({
            "reg": "nz", "dt": "androidtv", "client": "tvnz-tvnz-androidtv",
            "pf": "Regular", "allowpg": "true",
        })

        def _safe_int(val, default=0):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        def _first_text(lst):
            if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                return lst[0].get("n") or ""
            return ""

        # 1. Catalog lookup: series metadata → series_id
        self.status.emit(f"TVNZ: fetching series info for {slug}…")
        data = self._fetch_json(
            f"https://data-store-cdn.cms-api.tvnz.co.nz/content/urn/resource/catalog"
            f"/tvseries/{slug}?{_params}",
            _base_headers,
        )
        series_id = (data.get("data") or {}).get("id") if isinstance(data, dict) else None
        if not series_id:
            return []

        # 2. Seasons list
        self.status.emit("TVNZ: fetching seasons…")
        seasons_data = self._fetch_json(
            f"https://data-store-cdn.cms-api.tvnz.co.nz/content/series/{series_id}/seasons"
            f"?pageNumber=1&pageSize=99&sortBy=asc&sortOrder=desc&{_params}",
            _base_headers,
        )
        season_ids = [
            s.get("id") for s in (seasons_data.get("data") or [])
            if isinstance(s, dict) and s.get("id")
        ] if isinstance(seasons_data, dict) else []
        if not season_ids:
            return []

        # 3. Episodes for each season
        episodes = []
        for season_id in season_ids:
            self.status.emit(f"TVNZ: fetching season {season_id}…")
            try:
                eps_data = self._fetch_json(
                    f"https://data-store-cdn.cms-api.tvnz.co.nz/content/series/{series_id}/episodes"
                    f"?seasonId={season_id}&pageNumber=1&pageSize=99&sortBy=epnum&sortOrder=asc"
                    f"&{_params}",
                    _base_headers,
                )
            except Exception:
                continue
            for ep in (eps_data.get("data") or []) if isinstance(eps_data, dict) else []:
                if not isinstance(ep, dict):
                    continue
                ep_nu    = ep.get("nu") or ""
                ep_snum  = _safe_int(ep.get("snum"), 1)
                ep_num   = _safe_int(ep.get("epnum"), 0)
                ep_title = _first_text(ep.get("lodn"))
                ep_syn   = _first_text(ep.get("losd"))
                if not ep_nu:
                    continue
                episodes.append({
                    "series_no": str(ep_snum),
                    "title":     f"S{ep_snum:02}E{ep_num:02} {ep_title}".strip(),
                    "url":       f"https://tvnz.co.nz/tvepisode/{ep_nu}",
                    "synopsis":  ep_syn,
                })
        return episodes

    def _rktn(self, show_url: str) -> list:
        import re as _re, urllib.parse as _up, time as _time
        # Rakuten TV movies go direct to download — only TV shows reach this path
        if "/movies/" in show_url:
            return []
        m = _re.search(r'rakuten\.tv/(?:[a-z]+/)?tv_shows/([a-z0-9][a-z0-9-]+)', show_url)
        if not m:
            return []
        show_slug = m.group(1)

        # --- Step 1: authenticate ---
        self.status.emit("Rakuten TV: authenticating…")
        sess = _rktn_pair_device()

        # --- Step 2: fetch show info (seasons list) ---
        self.status.emit(f"Rakuten TV: fetching show '{show_slug}'…")
        base_params = {
            "classification_id": sess["classification_id"],
            "device_identifier": sess["device_identifier"],
            "device_serial":     sess["device_serial"],
            "locale":            sess["locale"],
            "market_code":       sess["market_code"],
            "session_uuid":      sess["session_uuid"],
            "timestamp":         str(int(_time.time())) + "005",
            "support_closed_captions": "true",
        }
        qs = _up.urlencode(base_params)
        # Try tv_shows endpoint for show overview first, fall back to seasons
        seasons = []
        for ep_base in ("tv_shows", "seasons"):
            try:
                show_data = self._fetch_json(f"https://gizmo.rakuten.tv/v3/{ep_base}/{show_slug}?{qs}")
                data_obj  = show_data.get("data") or {}
                tv_show   = data_obj.get("tv_show") or data_obj
                seasons   = tv_show.get("seasons") or []
                if seasons:
                    break
            except Exception:
                continue

        # --- Step 3: fetch episodes for each season ---
        episodes = []
        for season in seasons:
            season_id  = season.get("id", "")
            if not season_id:
                continue
            # season_number field varies — fall back to parsing slug (e.g. "sanctuary-1" → 1)
            season_num = season.get("season_number") or season.get("number") or 0
            if not season_num:
                sn_m = _re.search(r'-(\d+)$', season_id)
                season_num = int(sn_m.group(1)) if sn_m else 0
            self.status.emit(f"Rakuten TV: fetching season {season_num}…")
            qs2 = _up.urlencode({**base_params, "timestamp": str(int(_time.time())) + "005"})
            season_data = self._fetch_json(f"https://gizmo.rakuten.tv/v3/seasons/{season_id}?{qs2}")
            for ep in (season_data.get("data") or {}).get("episodes") or []:
                ep_id    = ep.get("id", "")
                ep_num   = ep.get("number", 0)
                ep_title = ep.get("title") or ep.get("display_name") or f"Episode {ep_num}"
                if not ep_id:
                    continue
                ep_url = (f"https://www.rakuten.tv/uk/tv_shows/{show_slug}"
                          f"/episodes/stream/{season_id}/{ep_id}")
                episodes.append({
                    "series_no": str(season_num),
                    "title":     f"S{int(season_num):02d}E{int(ep_num):02d}: {ep_title}" if season_num else ep_title,
                    "url":       ep_url,
                    "synopsis":  ep.get("short_plot", ""),
                })
        return episodes

    def _nrk(self, show_url: str) -> list:
        import re as _re
        m = _re.search(r'tv\.nrk\.no/serie/([^/?#]+)', show_url)
        if not m:
            return []
        slug = m.group(1)
        hdrs = {
            "Accept-Language": "nb-NO,de;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        }
        self.status.emit(f"NRK: fetching series info for {slug}…")
        series_data = self._fetch_json(f"https://psapi.nrk.no/tv/catalog/series/{slug}", hdrs)
        seasons = (series_data.get("_embedded") or {}).get("seasons") or []
        episodes = []
        for season in seasons:
            s_num = season.get("sequenceNumber") or 0
            try:
                s_num = int(s_num)
            except (TypeError, ValueError):
                s_num = 0
            for ep in (season.get("_embedded") or {}).get("episodes") or []:
                ep_id = ep.get("prfId") or ""
                if not ep_id:
                    continue
                raw_title = (ep.get("titles") or {}).get("title") or ep_id
                syn       = (ep.get("titles") or {}).get("subtitle") or ""
                # Title format is "N. Episode Name" — extract episode number
                nm = _re.match(r'^(\d+)\.\s*(.+)$', raw_title)
                e_num = int(nm.group(1)) if nm else 0
                ep_title = nm.group(2) if nm else raw_title
                episodes.append({
                    "series_no": str(s_num),
                    "title":     f"S{s_num:02}E{e_num:02} {ep_title}".strip(),
                    "url":       f"https://tv.nrk.no/serie/{slug}/sesong/{s_num}/episode/{ep_id}",
                    "synopsis":  syn,
                })
        return episodes

    def _ard(self, show_url: str) -> list:
        import re as _re, base64 as _b64
        hdrs = {
            "Accept-Language": "de-DE,de;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        }

        # Resolve grouping ID from URL
        # Format A: ardmediathek.de/serie/{name}/{item_id}  — item_id IS the grouping ID
        # Format B: ardmediathek.de/{broadcaster}/{slug}    — need editorial lookup first
        grouping_id = None
        m_serie = _re.search(r'ardmediathek\.de/serie/[^/]+/([a-zA-Z0-9+/=]{10,})', show_url)
        if m_serie:
            grouping_id = m_serie.group(1)
        else:
            m_broad = _re.search(r'ardmediathek\.de/([a-z]+)/([^/?#]+)', show_url)
            if m_broad:
                broadcaster, slug = m_broad.group(1), m_broad.group(2)
                self.status.emit(f"ARD: resolving series ID for {slug}…")
                editorial = self._fetch_json(
                    f"https://api.ardmediathek.de/page-gateway/pages/{broadcaster}"
                    f"/editorial/{slug}?embedded=true",
                    hdrs,
                )
                # Find the show.id from any EPISODE teaser
                for widget in (editorial.get("widgets") or []):
                    for teaser in (widget.get("teasers") or []):
                        show_id = (teaser.get("show") or {}).get("id") or ""
                        if teaser.get("coreAssetType") == "EPISODE" and show_id:
                            grouping_id = _b64.b64encode(
                                show_id.encode()
                            ).decode().rstrip("=")
                            break
                    if grouping_id:
                        break

        if not grouping_id:
            return []

        self.status.emit(f"ARD: fetching episode list…")
        data = self._fetch_json(
            f"https://api.ardmediathek.de/page-gateway/pages/ard/grouping/{grouping_id}"
            f"?seasoned=true&embedded=true",
            hdrs,
        )
        episodes = []
        for widget in (data.get("widgets") or []):
            compilation = widget.get("compilationType") or ""
            if compilation not in ("itemsOfSeason", "itemsOfShow"):
                continue
            for teaser in (widget.get("teasers") or []):
                if teaser.get("coreAssetType") != "EPISODE":
                    continue
                long_title = teaser.get("longTitle") or ""
                # Skip audio description duplicates
                if "Audiodeskription" in long_title or "Hörfassung" in long_title:
                    continue
                target_id = ((teaser.get("links") or {}).get("target") or {}).get("id") or ""
                if not target_id:
                    target_id = teaser.get("id") or ""
                if not target_id:
                    continue
                syn  = teaser.get("synopsis") or teaser.get("shortSynopsis") or ""
                sm   = _re.search(r'S(\d+)/E(\d+)', long_title)
                s_n  = int(sm.group(1)) if sm else 1
                e_n  = int(sm.group(2)) if sm else 0
                episodes.append({
                    "series_no": str(s_n),
                    "title":     long_title,
                    "url":       f"https://www.ardmediathek.de/video/episode/ard/{target_id}",
                    "synopsis":  syn,
                })
        return episodes

    def _zdf(self, show_url: str) -> list:
        import re as _re, json as _jmod, urllib.request as _ureq, urllib.error as _uerr
        m = _re.search(r'zdf\.de/serien/([^/?#]+)', show_url)
        if not m:
            return []
        slug = m.group(1).rstrip("/")
        ua = (
            "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/79.0.3945.79 Safari/537.36 "
            "DMOST/2.0.0 (; LGE; webOSTV; WEBOS6.3.2 03.34.95; W6_lm21a;)"
        )
        self.status.emit("ZDF: fetching API key…")
        try:
            req = _ureq.Request(
                "http://hbbtv.zdf.de/zdfm3/index.php",
                headers={"User-Agent": ua, "Accept-Language": "de-DE,de;q=0.8"},
            )
            with _ureq.urlopen(req, timeout=20) as r:
                page = r.read().decode("utf-8", errors="replace")
            km = _re.search(r'GLOBALS\.apikey\s*=\s*"([^"\n]+)"', page)
            if not km:
                return []
            api_auth = km.group(1)
        except Exception:
            return []

        # POST the full query rather than relying on a persisted query hash
        self.status.emit(f"ZDF: fetching episodes for {slug}…")
        query = (
            "query SmartCollection($canonical:String!,$eps:Int,$sort:[SortInput!]){"
            "smartCollectionByCanonical(canonical:$canonical){"
            "title seasons(first:50){nodes{seasonNumber episodes(first:$eps,sortBy:$sort){"
            "nodes{id canonical title subtitle editorialDate webUrl"
            " episodeInfo{episodeNumber seasonNumber}"
            " smartCollection{canonical}"
            "}}}}}}"
        )
        body = _jmod.dumps({
            "query": query,
            "variables": {
                "canonical": slug,
                "eps": 100,
                "sort": [{"field": "EDITORIAL_DATE", "direction": "ASC"}],
            },
        }, separators=(",", ":")).encode("utf-8")
        req = _ureq.Request(
            "https://api.zdf.de/graphql",
            data=body,
            headers={
                "User-Agent":     ua,
                "Accept-Language": "de-DE,de;q=0.8",
                "Api-Auth":       api_auth,
                "Content-Type":   "application/json",
                "Accept":         "application/json",
            },
        )
        try:
            with _ureq.urlopen(req, timeout=30) as r:
                data = _jmod.loads(r.read().decode("utf-8"))
        except _uerr.HTTPError as e:
            raise Exception(f"ZDF API HTTP {e.code}: {e.reason}")
        except Exception as e:
            raise

        collection = (data.get("data") or {}).get("smartCollectionByCanonical")
        if not collection:
            return []

        series_canonical = collection.get("canonical") or slug
        episodes = []
        for season in (collection.get("seasons") or {}).get("nodes") or []:
            s_default = season.get("seasonNumber") or 1
            for video in (season.get("episodes") or {}).get("nodes") or []:
                ep_info = video.get("episodeInfo") or {}
                s_n     = ep_info.get("seasonNumber") or s_default
                e_n     = ep_info.get("episodeNumber") or 0
                title   = video.get("title") or ""
                syn     = video.get("subtitle") or ""
                # Prefer webUrl; fall back to constructing from canonical
                ep_url  = video.get("webUrl") or ""
                if not ep_url:
                    ep_slug = video.get("canonical") or ""
                    sc      = (video.get("smartCollection") or {}).get("canonical") or series_canonical
                    if ep_slug:
                        ep_url = f"https://www.zdf.de/video/serie/{sc}/{ep_slug}"
                if not ep_url:
                    continue
                try:
                    s_n = int(s_n); e_n = int(e_n)
                except (TypeError, ValueError):
                    pass
                label = f"S{s_n:02}E{e_n:02} {title}".strip() if e_n else title
                episodes.append({
                    "series_no": str(s_n),
                    "title":     label,
                    "url":       ep_url,
                    "synopsis":  syn,
                })
        return episodes

    def _roku(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        HDRS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json, text/plain, */*",
            "Origin":     "https://therokuchannel.roku.com",
            "Referer":    "https://therokuchannel.roku.com/",
        }
        CONTENT_BASE = (
            "https://therokuchannel.roku.com/api/v2/homescreen/content/"
            "https%3A%2F%2Fcontent.sr.roku.com%2Fcontent%2Fv1%2Froku-trc%2F"
        )

        def _fetch(cid):
            req = _ur.Request(f"{CONTENT_BASE}{cid}", headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                raw = r.read()
                if not raw.strip():
                    raise RuntimeError("Empty response — Roku content API is geofenced to the US")
                return _json.loads(raw.decode("utf-8"))

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "episode"

        m = _re.search(r"/details/([a-z0-9-]+)", url)
        if not m:
            return []
        series_id = m.group(1)

        data = _fetch(series_id)
        if data.get("type") in ("movie", "tvspecial"):
            return []  # movies route via _on_show_selected before reaching here

        ep_stubs = data.get("episodes") or []
        episodes  = []
        seen      = set()

        self.status.emit(f"Roku: fetching {len(ep_stubs)} episode(s)…")
        for stub in ep_stubs:
            ep_id = (stub.get("meta") or {}).get("id", "")
            if not ep_id or ep_id in seen:
                continue
            seen.add(ep_id)
            try:
                ep = _fetch(ep_id)
            except Exception:
                ep = stub
            s_no   = str(ep.get("seasonNumber") or stub.get("seasonNumber") or 0)
            ep_num = ep.get("episodeNumber") or stub.get("episodeNumber") or 0
            title  = ep.get("title") or stub.get("title") or f"Episode {ep_num}"
            label  = f"Ep {ep_num}: {title}" if ep_num else title
            ep_url = f"https://therokuchannel.roku.com/details/{ep_id}/{_slugify(title)}"
            episodes.append({
                "series_no": s_no,
                "title":     label,
                "synopsis":  (ep.get("descriptions") or {}).get("250", {}).get("text") or "",
                "url":       ep_url,
            })
        return episodes

    def _cbs(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json, urllib.parse as _up
        BASE  = "https://cbsdigital.cbs.com"
        TOKEN = "ABBsaBMagMmYLUc9iXB0lXEKsUQ0/MwRn6z3Tg0KKQaH7Q6QGqJcABwlBP4XiMR1b0Q="
        HDRS  = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
            "Accept":     "application/json",
        }

        def _get(path, params=None):
            p = {"at": TOKEN}
            if params:
                p.update(params)
            req = _ur.Request(f"{BASE}{path}?{_up.urlencode(p)}", headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                return _json.loads(r.read().decode("utf-8"))

        # Direct episode URL: /shows/video/{id}/
        ep_m = _re.search(r"/shows/video/([A-Za-z0-9_-]+)", url)
        if ep_m:
            return []  # single episode — opts panel handles it

        # Series URL: /shows/{slug}/
        slug_m = _re.search(r"/shows/([^/?#]+)/?$", url)
        if not slug_m:
            return []
        slug = slug_m.group(1)

        # 1. Get show metadata and season list
        show_data = _get(f"/apps-api/v3.0/androidphone/shows/slug/{slug}.json")
        links = next(
            (x.get("links") for x in (show_data.get("showMenu") or [])
             if x.get("device_app_id") == "all_platforms"),
            None,
        )
        config = None
        if links:
            config = next(
                (x.get("videoConfigUniqueName") for x in links
                 if (x.get("title") or "").strip() == "Episodes"),
                None,
            )
        show_obj = next(
            (x for x in (show_data.get("show") or {}).get("results", [])
             if (x.get("type") or "").strip() == "show"),
            None,
        )
        if not show_obj or not config:
            raise RuntimeError("Could not find show data — check the URL is a CBS series page")

        show_id = show_obj.get("show_id")
        seasons = [x.get("seasonNum") for x in
                   (show_data.get("available_video_seasons") or {}).get("itemList", [])]

        # 2. Get section ID for "Full Episodes"
        section_data = _get(
            f"/apps-api/v2.0/androidphone/shows/{show_id}/videos/config/{config}.json",
            {"platformType": "apps", "rows": "1", "begin": "0"},
        )
        section = next(
            (x["sectionId"] for x in (section_data.get("videoSectionMetadata") or [])
             if x.get("title") == "Full Episodes"),
            None,
        )
        if not section:
            raise RuntimeError("Could not find episode section for this show")

        # 3. Fetch episodes per season
        episodes = []
        for season in seasons:
            res = _get(
                f"/apps-api/v2.0/androidphone/videos/section/{section}.json",
                {"begin": "0", "rows": "999", "params": f"seasonNum={season}", "seasonNum": season},
            )
            for ep in (res.get("sectionItems") or {}).get("itemList", []):
                if not ep.get("fullEpisode"):
                    continue
                cid     = ep.get("contentId", "")
                s_no    = str(ep.get("seasonNum", 0))
                ep_num  = ep.get("episodeNum") or ep.get("positionNum") or 0
                title   = ep.get("label") or f"Episode {ep_num}"
                label   = f"Ep {ep_num}: {title}" if ep_num else title
                episodes.append({
                    "series_no": s_no,
                    "title":     label,
                    "synopsis":  ep.get("description") or "",
                    "url":       f"https://www.cbs.com/shows/video/{cid}/",
                })
        return episodes

    def _cwtv(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0 (Linux; Android 11; Smart TV Build/AR2101; wv)",
                "Accept": "application/json"}
        m = _re.search(r"/(?:shows|series)/([^/?#]+)", url)
        if not m:
            return []
        slug = m.group(1)
        req = _ur.Request(
            f"https://data.cwtv.com/feed/app-2/videos/show_{slug}/type_episodes/apiversion_25/device_androidtv",
            headers=HDRS,
        )
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8"))
        episodes = []
        for ep in data.get("items") or []:
            if ep.get("fullep") != 1:
                continue
            vid = ep.get("bc_video_id") or ""
            if not vid:
                continue
            s_no   = str(ep.get("season") or 0)
            ep_num = int(ep.get("episode_in_season") or 0)
            title  = ep.get("title") or f"Episode {ep_num}"
            label  = f"Ep {ep_num}: {title}" if ep_num else title
            ep_url = f"https://www.cwtv.com/series/{slug}/?play={vid}"
            episodes.append({
                "series_no": s_no,
                "title":     label,
                "synopsis":  ep.get("description_long") or ep.get("description") or "",
                "url":       ep_url,
            })
        return episodes

    def _pbs(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        import http.cookiejar as _cj, os as _os
        UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0"
        HDRS = {"User-Agent": UA, "Accept": "application/json, text/html, */*"}
        UUID_PAT = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'

        m = _re.search(r"/show/([^/?#]+)", url)
        if not m:
            return []
        slug = m.group(1)

        # Load PBS cookies for Passport authentication if available
        from pathlib import Path as _Path
        jar = _cj.MozillaCookieJar()
        _cfg = load_config()
        cookie_path = _Path(_cfg.get("install_dir", "")) / "Cookies" / "PBS.txt"
        if cookie_path.exists():
            jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
        opener = _ur.build_opener(_ur.HTTPCookieProcessor(jar))

        def _get(u):
            req = _ur.Request(u, headers=HDRS)
            with opener.open(req, timeout=20) as r:
                return _json.loads(r.read().decode("utf-8"))

        # Parse season CIDs from show page HTML
        req = _ur.Request(f"https://www.pbs.org/show/{slug}/", headers=HDRS)
        with opener.open(req, timeout=20) as r:
            html = r.read().decode("utf-8")

        seasons = []
        seen_cids = set()
        for url_m in _re.finditer(
            r'pbsorg/screens/shows/[^/]+/seasons/(' + UUID_PAT + r')/', html
        ):
            cid = url_m.group(1)
            if cid in seen_cids:
                continue
            seen_cids.add(cid)
            window = html[max(0, url_m.start() - 800):url_m.start()]
            all_ords = _re.findall(r'ordinal[\\\"]*\s*:\s*(\d+)', window)
            ordinal  = int(all_ords[-1]) if all_ords else (len(seasons) + 1)
            seasons.append((cid, ordinal))
        seasons.sort(key=lambda x: x[1], reverse=True)

        episodes = []
        for season_cid, season_num in seasons:
            try:
                eps = _get(f"https://www.pbs.org/api/show/{slug}/season/{season_cid}/episodes/")
            except Exception:
                continue
            for ep in (eps if isinstance(eps, list) else []):
                ep_slug = ep.get("slug") or ""
                if not ep_slug:
                    continue
                parent  = ep.get("parent") or {}
                ep_num  = int(parent.get("ordinal") or 0)
                title   = ep.get("title") or f"Episode {ep_num}"
                label   = f"Ep {ep_num}: {title}" if ep_num else title
                episodes.append({
                    "series_no": str(season_num),
                    "title":     label,
                    "synopsis":  ep.get("description_short") or "",
                    "url":       f"https://www.pbs.org/video/{ep_slug}/",
                })

        # Fetch specials (season 0)
        try:
            specials = _get(f"https://www.pbs.org/api/show/{slug}/specials/")
            if isinstance(specials, list):
                specials.sort(key=lambda x: x.get("premiere_date") or "")
                for i, sp in enumerate(specials, start=1):
                    ep_slug = sp.get("slug") or ""
                    if not ep_slug or sp.get("slug") == sp.get("parent", {}).get("slug"):
                        continue
                    title = sp.get("title") or f"Special {i}"
                    episodes.append({
                        "series_no": "0",
                        "title":     f"Special {i}: {title}",
                        "synopsis":  sp.get("description_short") or "",
                        "url":       f"https://www.pbs.org/video/{ep_slug}/",
                    })
        except Exception:
            pass

        return episodes

    def _crav(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        token = _crav_get_graphql_token()
        if not token:
            raise RuntimeError("No CRAV credentials found. Add username/password to EnvyCore/Cookies/CRAV.txt.")
        HDRS = {
            "User-Agent":    "Dalvik/2.1.0 (Linux; U; Android 11; SHIELD Android TV Build/RQ1A.210105.003)",
            "Content-Type":  "application/json",
            "Accept":        "*/*",
            "authorization": f"Bearer {token}",
        }
        SESSION_CTX = {"userMaturity": "ADULT", "userLanguage": "EN"}

        # Extract numeric media ID from URL, e.g. /en/series/succession-58324 → 58324
        m = _re.search(r'-(\d+)(?:/|$)', url)
        if not m:
            return []
        media_id = m.group(1)

        def _gql(payload):
            req = _ur.Request("https://rte-api.bellmedia.ca/graphql",
                              data=_json.dumps(payload).encode(), headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                return _json.loads(r.read().decode())

        # Step 1: get show seasons
        self.status.emit("Crave: fetching season list…")
        show_data = _gql({
            "query": (
                "query GetShowpage($sessionContext:SessionContext!,$ids:[String!]!){"
                "medias(sessionContext:$sessionContext ids:$ids){"
                "title seasons{id title seasonNumber}}}"
            ),
            "variables": {"ids": [media_id], "sessionContext": SESSION_CTX},
        })
        medias = ((show_data.get("data") or {}).get("medias") or [])
        if not medias:
            return []
        seasons = medias[0].get("seasons") or []

        # Step 2: fetch episodes per season using contentsBySeasonId (returns all episodes)
        GQL_EPS = (
            "query GetContentBySeasonId($sessionContext:SessionContext!,$id:String!,$contentFormat:ContentFormatRequest){"
            "contentsBySeasonId(sessionContext:$sessionContext id:$id contentFormat:$contentFormat){"
            "id title episodeNumber seasonNumber path shortDescription}}"
        )
        episodes = []
        for season in seasons:
            season_id  = season.get("id") or ""
            season_num = season.get("seasonNumber") or 0
            if not season_id:
                continue
            self.status.emit(f"Crave: fetching season {season_num}…")
            try:
                eps_data = _gql({
                    "query":     GQL_EPS,
                    "variables": {
                        "id":            season_id,
                        "sessionContext": SESSION_CTX,
                        "contentFormat": {"format": "LONGFORM"},
                    },
                })
                for item in (eps_data.get("data") or {}).get("contentsBySeasonId") or []:
                    path   = item.get("path") or ""
                    title  = item.get("title") or ""
                    ep_num = int(item.get("episodeNumber") or 0)
                    if not path:
                        continue
                    label = f"Ep {ep_num}: {title}" if ep_num else title
                    episodes.append({
                        "series_no": str(season_num),
                        "title":     label,
                        "synopsis":  item.get("shortDescription") or "",
                        "url":       f"https://www.crave.ca/{path.lstrip('/')}",
                    })
            except Exception:
                continue
        return episodes

    def _cbc(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        m = _re.match(r"^(?:https?://(?:www\.)?gem\.cbc\.ca/)?([a-zA-Z0-9_-]+)", url)
        if not m:
            return []
        slug = m.group(1)
        BASE = "https://services.radio-canada.ca"
        req = _ur.Request(f"{BASE}/ott/catalog/v2/gem/show/{slug}?device=web",
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        content_type = (data.get("contentType") or "").lower()
        content = data.get("content") or []
        episodes = []
        if content_type in ("film", "movie", "standalone"):
            # Single movie — find the first lineup with items
            for section in content:
                if (section.get("title") or "").lower() in ("episodes", "trailers", "extras"):
                    continue
                for lineup in (section.get("lineups") or []):
                    for item in (lineup.get("items") or []):
                        item_url = item.get("url") or ""
                        title    = item.get("title") or ""
                        if not item_url or not title:
                            continue
                        if not item_url.startswith("http"):
                            item_url = f"https://gem.cbc.ca/{item_url.lstrip('/')}"
                        episodes.append({"series_no": "0", "title": title,
                                         "synopsis": item.get("description") or "", "url": item_url})
                if episodes:
                    break
        else:
            # Series — find Episodes / Parts section
            ep_section = next(
                (s for s in content if (s.get("title") or "").lower() in ("episodes", "parts")), None
            )
            if not ep_section:
                return []
            for lineup in (ep_section.get("lineups") or []):
                season_num = str(lineup.get("seasonNumber") or 0)
                for item in (lineup.get("items") or []):
                    if (item.get("mediaType") or "").lower() != "episode":
                        continue
                    item_url = item.get("url") or ""
                    raw_title = item.get("title") or ""
                    ep_num    = int(item.get("episodeNumber") or 0)
                    if not item_url:
                        continue
                    # title is often "S1E1. Episode name" — strip the prefix
                    parts = raw_title.split(".", 1)
                    ep_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                    label = f"Ep {ep_num}: {ep_name}" if ep_num else ep_name
                    if not item_url.startswith("http"):
                        item_url = f"https://gem.cbc.ca/{item_url.lstrip('/')}"
                    episodes.append({"series_no": season_num, "title": label,
                                     "synopsis": item.get("description") or "", "url": item_url})
        return episodes

    def _threenow(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        # Extract show_id — last path segment, e.g. .../shows/big-mood/1713218432373
        m = _re.search(r'/([A-Za-z0-9][A-Za-z0-9_-]*)/?(?:\?.*)?$', url)
        if not m:
            return []
        show_id = m.group(1)
        req = _ur.Request(
            f"https://now-api.fullscreen.nz/v5/shows/{show_id}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        episodes = []
        for season in (data.get("seasons") or []):
            season_num = str(season.get("seasonNumber") or 0)
            for ep in (season.get("episodes") or []):
                ep_num  = int(ep.get("episode") or 0)
                title   = ep.get("name") or ep.get("title") or ""
                video_id = ep.get("videoId") or ""
                if not video_id:
                    continue
                label = f"Ep {ep_num}: {title}" if ep_num and title else (title or f"Ep {ep_num}")
                episodes.append({
                    "series_no": season_num,
                    "title":     label,
                    "synopsis":  ep.get("synopsis") or "",
                    "url":       f"https://www.threenow.co.nz/shows/{show_id}/{video_id}",
                })
        return episodes

    def _aubc(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        # URL: https://iview.abc.net.au/show/{slug}
        m = _re.search(r'/show/([A-Za-z0-9][A-Za-z0-9_-]*)/?(?:\?.*)?$', url)
        if not m:
            return []
        slug = m.group(1)
        BASE = "https://api.iview.abc.net.au"
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "accept-language": "en-US,en;q=0.8"}
        # Check show type
        req = _ur.Request(f"{BASE}/v3/show/{slug}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            show_data = _json.loads(r.read().decode())
        label = (show_data.get("type") or "").lower()
        if label in ("feature", "movie"):
            # Single movie — return as one entry
            title = show_data.get("title") or slug
            return [{"series_no": "0", "title": title, "synopsis": show_data.get("description") or "", "url": url}]
        # Series — fetch all seasons
        req2 = _ur.Request(f"{BASE}/v3/series/{slug}", headers=HDRS)
        with _ur.urlopen(req2, timeout=30) as r:
            series_data = _json.loads(r.read().decode())
        seasons = series_data if isinstance(series_data, list) else [series_data]
        # De-duplicate seasons by id
        seen_ids = set()
        unique_seasons = []
        for s in seasons:
            sid = s.get("id")
            if sid not in seen_ids:
                seen_ids.add(sid)
                unique_seasons.append(s)
        episodes = []
        for season in unique_seasons:
            series_id = season.get("id") or ""
            # Season number from series_id suffix, e.g. "show-slug-2" → 2
            season_num = str(int(series_id.rsplit("-", 1)[-1])) if series_id and series_id.rsplit("-", 1)[-1].isdigit() else "0"
            for ep in (season.get("_embedded") or {}).get("videoEpisodes", {}).get("items") or []:
                ep_id = ep.get("id") or ""
                if not ep_id:
                    continue
                ep_num_m = _re.search(r"Episode (\d+)", ep.get("displaySubtitle") or "")
                ep_num   = int(ep_num_m.group(1)) if ep_num_m else 0
                # Episode name from d_episode_name, strip "S\d+ Episode \d+ " prefix
                ep_name_raw = (ep.get("analytics") or {}).get("dataLayer", {}).get("d_episode_name") or ""
                name_m = _re.search(r"S\d+\s+Episode\s+\d+\s+(.*)", ep_name_raw)
                ep_name = name_m.group(1).strip() if name_m else (ep.get("displaySubtitle") or "")
                label_str = f"Ep {ep_num}: {ep_name}" if ep_num and ep_name else ep_name or f"Ep {ep_num}"
                episodes.append({
                    "series_no": season_num,
                    "title":     label_str,
                    "synopsis":  ep.get("description") or "",
                    "url":       f"https://iview.abc.net.au/video/{ep_id}",
                })
        return episodes

    def _seven(self, url: str) -> list:
        import re as _re, urllib.request as _ur, urllib.parse as _up, json as _json
        # URL: https://7plus.com.au/{slug}
        m = _re.match(r'https?://7plus\.com\.au/([^?/#]+)', url)
        if not m:
            return []
        slug = m.group(1)
        HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "x-swm-apikey": "kGcrNnuPClrkynfnKwG8IA/NhVG6ut5nPEdWF2jscvE="}
        # Get market_id
        mreq = _ur.Request("https://market-cdn.swm.digital/v1/market/ip?apikey=web", headers=HDRS)
        try:
            with _ur.urlopen(mreq, timeout=8) as r:
                market_id = _json.loads(r.read().decode()).get("_id", 4)
        except Exception:
            market_id = 4
        base_params = _up.urlencode({
            "platform-id": "androidtv", "market-id": market_id,
            "platform-version": "5.25.0.0", "api-version": "5.9.0.0",
        })
        # Fetch show content page
        req = _ur.Request(
            f"https://component-cdn.swm.digital/content/{slug}?{base_params}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            content = _json.loads(r.read().decode())
        # Navigate: shelfContainer → Episodes tab → Season/Year/Bulletin → season buttons → inner component id
        shelf_container = next(
            (x for x in content.get("items", []) if x.get("type") == "shelfContainer"), {})
        episodes_shelf = next(
            (x for x in shelf_container.get("items", []) if x.get("title") == "Episodes"), {})
        seasons_container = next(
            (x for x in episodes_shelf.get("items", [])
             if (x.get("title") or "").lower() in ("season", "year", "bulletin")), {})
        season_ids = [
            btn.get("items", [{}])[0].get("id")
            for btn in seasons_container.get("items", [])
            if btn.get("items") and btn.get("items")[0].get("id")
        ]
        if not season_ids:
            return []
        episodes = []
        for season_id in season_ids:
            comp_req = _ur.Request(
                f"https://component.swm.digital/v2/component/{slug}"
                f"?component-id={season_id}&{base_params}", headers=HDRS)
            try:
                with _ur.urlopen(comp_req, timeout=15) as r:
                    comp = _json.loads(r.read().decode())
            except Exception:
                continue
            for ep in (comp.get("mediaItems") or []):
                player  = ep.get("playerData") or {}
                card    = ep.get("cardData") or {}
                ep_id   = player.get("episodePlayerId") or ""
                if not ep_id:
                    continue
                alt_tag = (card.get("image") or {}).get("altTag") or ""
                sm = _re.search(r"Season\s+(\d+)\s+Episode\s+(\d+)", alt_tag, _re.IGNORECASE)
                season_num = sm.group(1) if sm else "0"
                ep_num     = int(sm.group(2)) if sm else 0
                # cardData.title is like "4. Soup To Nuts" — strip leading number+period
                raw_title = card.get("title") or ""
                title_m = _re.match(r"^\d+\.\s*(.+)", raw_title)
                ep_name = title_m.group(1).strip() if title_m else raw_title
                label = f"Ep {ep_num}: {ep_name}" if ep_num and ep_name else (ep_name or f"Ep {ep_num}")
                episodes.append({
                    "series_no": season_num,
                    "title":     label,
                    "synopsis":  (ep.get("infoPanelData") or {}).get("description") or "",
                    "url":       f"https://7plus.com.au/{slug}?episode-id={ep_id}",
                })
        return episodes

    def _ten(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        m = _re.match(r'https?://10\.com\.au/([^?/#]+)', url)
        if not m:
            return []
        slug = m.group(1)
        HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*"}
        req = _ur.Request(f"https://10.com.au/{slug}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
        # Brace-balance extract const showPageData = {...}
        idx = html.find('const showPageData = ')
        if idx < 0:
            return []
        json_start = html.index('{', idx)
        depth, json_end = 0, json_start
        for i, ch in enumerate(html[json_start:], json_start):
            if ch == '{':   depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        try:
            page_data = _json.loads(html[json_start:json_end])
        except Exception:
            return []
        # seasonList is a direct key at the top level
        season_list = page_data.get("seasonList") or []
        if not season_list:
            return []
        episodes = []
        API_HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        for season in season_list:
            season_slug = season.get("slug") or ""
            if not season_slug:
                continue
            api_req = _ur.Request(f"https://10.com.au/api/shows/{slug}/episodes/{season_slug}", headers=API_HDRS)
            try:
                with _ur.urlopen(api_req, timeout=20) as r:
                    season_data = _json.loads(r.read().decode())
            except Exception:
                continue
            content = season_data.get("content") or []
            if not content:
                continue
            for comp in (content[0].get("components") or []):
                if comp.get("title") != "Episodes":
                    continue
                for slide in (comp.get("slides") or []):
                    if slide.get("contentType") != "video":
                        continue
                    # cardTitle: "S1 Ep. 1 - Burnt Food"
                    card_title = slide.get("cardTitle") or ""
                    sm = _re.search(r'S(\d+)\s+Ep\.?\s*(\d+)\s*[-–]?\s*(.*)', card_title, _re.IGNORECASE)
                    season_num = sm.group(1) if sm else "0"
                    ep_num     = int(sm.group(2)) if sm else 0
                    ep_name    = (sm.group(3) or "").strip() if sm else card_title
                    label = f"Ep {ep_num}: {ep_name}" if ep_num and ep_name else (ep_name or f"Ep {ep_num}")
                    card_link = slide.get("cardLink") or ""
                    episodes.append({
                        "series_no": season_num,
                        "title":     label,
                        "synopsis":  slide.get("cardDescription") or "",
                        "url":       f"https://10.com.au{card_link}" if card_link else url,
                    })
                break
        return episodes

    def _sbs(self, url: str) -> list:
        import re as _re, urllib.request as _ur, json as _json
        HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json", "Origin": "https://www.sbs.com.au"}

        # Movie
        mm = _re.match(r'https?://www\.sbs\.com\.au/ondemand/movie/([^/?#]+)', url)
        if mm:
            movie_slug = mm.group(1)
            req = _ur.Request(f"https://catalogue.pr.sbsod.com/movies/{movie_slug}", headers=HDRS)
            with _ur.urlopen(req, timeout=20) as r:
                data = _json.loads(r.read().decode())
            title = data.get("title") or movie_slug
            media_id = data.get("mpxMediaID") or ""
            movie_url = f"https://www.sbs.com.au/ondemand/movie/{movie_slug}/{media_id}" if media_id else url
            return [{"series_no": "1", "title": title, "synopsis": data.get("description") or "", "url": movie_url}]

        # TV Series
        m = _re.match(r'https?://www\.sbs\.com\.au/ondemand/tv-series/([^/?#]+)', url)
        if not m:
            return []
        series_slug = m.group(1)
        req = _ur.Request(f"https://catalogue.pr.sbsod.com/tv-series/{series_slug}", headers=HDRS)
        with _ur.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode())
        episodes = []
        for season in (data.get("seasons") or []):
            s_no = str(season.get("seasonNumber") or "0")
            for ep in (season.get("episodes") or []):
                ep_no = ep.get("episodeNumber") or 0
                media_id = ep.get("mpxMediaID") or ""
                title = ep.get("title") or ""
                label = f"Ep {ep_no}: {title}" if ep_no and title else title or f"Ep {ep_no}"
                ep_url = f"https://www.sbs.com.au/ondemand/tv-series/{series_slug}/{media_id}" if media_id else url
                episodes.append({
                    "series_no": s_no,
                    "title":     label,
                    "synopsis":  ep.get("description") or "",
                    "url":       ep_url,
                })
        return episodes

    def _vm(self, url: str) -> list:
        import re as _re, urllib.parse as _up
        BASE_PARAMS = _up.urlencode({
            "key":      "821254297041614280861178657602",
            "cc":       "IE",
            "lang":     "en",
            "platform": "chrome",
        })
        HEADERS = {
            "Origin":  "https://play.virginmediatelevision.ie",
            "Referer": "https://play.virginmediatelevision.ie/",
        }

        series_m = _re.search(r"/shows/([0-9a-f-]{36})", url, _re.I)
        if not series_m:
            return []
        series_id = series_m.group(1)

        data = None
        for endpoint in [
            f"https://v6-metadata-cf.simplestreamcdn.com/api/series/{series_id}?{BASE_PARAMS}",
            f"https://v6-metadata.simplestreamcdn.com/api/series/{series_id}?{BASE_PARAMS}",
        ]:
            try:
                data = self._fetch_json(endpoint, HEADERS)
                break
            except Exception:
                continue
        if not data:
            return []

        series    = ((data.get("response") or {}).get("series") or {})
        episodes  = []
        seen      = set()

        def _slugify(s):
            return _re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "episode"

        for s_idx, season in enumerate(series.get("seasons") or []):
            # VM API has no numeric season field — use the season title (e.g. "Series 1")
            s_no = str(season.get("title") or f"Series {s_idx + 1}").strip()
            for ep in season.get("tiles") or season.get("episodes") or []:
                # uvid is the downloadable video ID; id can differ
                vid = str(ep.get("uvid") or ep.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                ep_num   = ep.get("episode") or ep.get("series_episode") or 0
                ep_title = ep.get("title") or ep.get("name") or f"Episode {ep_num}"
                label    = f"Ep {ep_num}: {ep_title}" if ep_num else ep_title
                ep_url   = f"https://play.virginmediatelevision.ie/watch/vod/{vid}/{_slugify(ep_title)}"
                episodes.append({
                    "series_no": s_no,
                    "title":     label,
                    "synopsis":  ep.get("synopsis") or ep.get("description") or "",
                    "url":       ep_url,
                })
        return episodes


class ExtendedServiceWorker(QThread):
    """
    Runs uv run envied dl [--select-titles] SERVICE URL directly.
    Used by the Extended Services panel.
    select_titles=True patches the envied selector for the Qt episode picker.
    """
    log_line  = pyqtSignal(str)
    raw_bytes = pyqtSignal(bytes)
    episode   = pyqtSignal(str)   # label shown above the terminal
    done      = pyqtSignal(bool)  # success flag when all complete
    finished  = pyqtSignal()

    def __init__(self, uv_exe, install_dir, service, url,
                 extra_args=None, select_titles=False):
        super().__init__()
        self.uv_exe        = str(uv_exe)
        self.install_dir   = Path(install_dir)
        self.service       = service
        self.url           = url
        self.extra_args    = extra_args or []
        self.select_titles = select_titles
        self._cancelled    = False

    def run(self):
        import threading as _th
        import queue as _queue
        import os as _os2
        import subprocess as _sp2
        import re as _re2

        try:
            cmd = [self.uv_exe, "run", "--no-sync", "envied", "dl"]
            cmd += self.extra_args
            cmd += [self.service, self.url]
            self.log_line.emit(f"[extended] {' '.join(cmd)}")

            _ansi_re = _re2.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\][^\x07]*\x07')

            env = _os2.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONUTF8"]       = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONWARNINGS"]   = "ignore"
            env["WT_SESSION"]       = env.get("WT_SESSION") or "EnvyUI"
            env["FORCE_COLOR"]      = "1"
            env["COLORTERM"]        = "truecolor"
            env["TERM"]             = "xterm-256color"
            venv_scripts = str(self.install_dir / ".venv" / "Scripts")
            env["PATH"] = venv_scripts + ";" + r"C:\Tools\bin" + ";" + env.get("PATH", "")

            # Use a real PTY (winpty) so rich emits full colour + cursor-up animation,
            # exactly the same as the main download panel.
            try:
                from winpty import PtyProcess as _PtyProcess
                _use_pty = True
            except ImportError:
                _use_pty = False

            _proc_pid = [None]

            if _use_pty:
                _pty_cmd = ' '.join(f'"{a}"' if ' ' in a else a for a in cmd)
                _pty = _PtyProcess.spawn(_pty_cmd, cwd=str(self.install_dir),
                                         env=env, dimensions=(32, 120))
                _proc_pid[0] = getattr(_pty, 'pid', None)

                class _PtyPipe:
                    def __init__(self, p): self._p = p; self._eof = False
                    def read(self, n):
                        if self._eof: return b''
                        try:
                            d = self._p.read(n)
                            if d is None or d == '': self._eof = True; return b''
                            return d.encode('utf-8', errors='replace')
                        except EOFError: self._eof = True; return b''
                        except Exception: self._eof = True; return b''
                pipe = _PtyPipe(_pty)

                def _kill():
                    try: _pty.terminate()
                    except Exception: pass

                def _wait():
                    try: _pty.wait()
                    except Exception: pass
            else:
                proc = _sp2.Popen(
                    cmd, cwd=str(self.install_dir),
                    stdout=_sp2.PIPE, stderr=_sp2.STDOUT,
                    env=env, text=False, bufsize=0,
                    creationflags=_sp2.CREATE_NO_WINDOW | _sp2.CREATE_NEW_PROCESS_GROUP,
                )
                _proc_pid[0] = proc.pid
                pipe = proc.stdout

                def _kill():
                    try:
                        _sp2.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                 creationflags=_sp2.CREATE_NO_WINDOW, capture_output=True)
                    except Exception:
                        try: proc.terminate()
                        except Exception: pass

                def _wait():
                    proc.wait()

            _stdout_q = _queue.Queue()

            def _reader(p, q):
                try:
                    while True:
                        chunk = p.read(256)
                        if not chunk:
                            if getattr(p, '_eof', False): break
                            if not hasattr(p, '_eof'): break
                            import time as _t; _t.sleep(0.05); continue
                        q.put(chunk)
                except Exception:
                    pass
                finally:
                    q.put(None)

            _th.Thread(target=_reader, args=(pipe, _stdout_q), daemon=True).start()

            _rawbuf = b''
            while True:
                if self._cancelled:
                    _kill()
                    break
                try:
                    chunk = _stdout_q.get(timeout=0.5)
                except _queue.Empty:
                    continue
                if chunk is None:
                    break
                # Send raw bytes straight to xterm.js — handles ANSI, cursor-up, colours
                self.raw_bytes.emit(chunk)
                # Also scan cleaned lines for the app-wide log tab
                _rawbuf += chunk
                while b'\n' in _rawbuf:
                    n_pos = _rawbuf.find(b'\n')
                    ln = _rawbuf[:n_pos]
                    _rawbuf = _rawbuf[n_pos + 1:]
                    if ln.endswith(b'\r'):
                        ln = ln[:-1]
                    clean = _ansi_re.sub('', ln.decode('utf-8', errors='replace')).strip()
                    if clean:
                        self.log_line.emit(clean)

            # Flush any remaining bytes (last partial line, e.g. "Processed all titles…")
            if _rawbuf:
                self.raw_bytes.emit(_rawbuf)
                clean = _ansi_re.sub('', _rawbuf.decode('utf-8', errors='replace')).strip()
                if clean:
                    self.log_line.emit(clean)

            _wait()
            rc = getattr(proc if not _use_pty else _pty, 'returncode', None) or 0
            success = (rc == 0)
            status = "✓ complete" if success else f"✗ failed (code {rc})"
            result_line = f"Download: {status}"
            self.raw_bytes.emit((result_line + "\r\n").encode())
            self.log_line.emit(result_line)
            self.done.emit(success)
        except Exception as e:
            import traceback
            self.log_line.emit(f"[extended] Error: {e}")
            self.log_line.emit(traceback.format_exc())
            self.done.emit(False)
        finally:
            self.finished.emit()



# ── Install worker ────────────────────────────────────────────────────────────

def _run_hidden(cmd, cwd=None, env=None):
    base_env = os.environ.copy()
    base_env["PYTHONUTF8"] = "1"
    base_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        base_env.update(env)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=base_env, startupinfo=si,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

def _run_cmd(cmd, cwd=None, env=None):
    proc = _run_hidden(cmd, cwd=cwd, env=env)
    for line in proc.stdout:
        yield line.rstrip()
    proc.wait()
    return proc.returncode


class _UpdateCheckThread(QThread):
    """Checks GitHub for the APP_VERSION string in main's launcher .py."""
    result_ready = pyqtSignal(str, str)   # (remote_version, local_version)

    def __init__(self, local_version: str):
        super().__init__()
        self._local = local_version or ""

    def run(self):
        remote = ""
        try:
            import urllib.request as _ur, re as _re
            raw_url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/"
                "envy_launcher.py"
            )
            with _ur.urlopen(raw_url, timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            m = _re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']',
                            text, _re.MULTILINE)
            if m:
                remote = m.group(1)
        except Exception:
            pass
        self.result_ready.emit(remote, self._local)


class _ProviderCheckThread(QThread):
    """Checks metadata provider availability once at startup."""
    # Emit comma-separated "name:1" or "name:0" pairs e.g. "IMDBApi:0,TMDB:1,OMDb:1,SIMKL:0"
    result_ready = pyqtSignal(str)

    def run(self):
        try:
            import re as _re, urllib.request as _ur
            from pathlib import Path as _P

            # Read keys directly from envied.yaml (envied is in venv, not system Python)
            cfg = load_config()
            yaml_path = _P(cfg.get("install_dir", "")) / "packages/envied/src/envied/envied.yaml"
            tmdb_key = ""
            omdb_key = ""
            simkl_key = ""
            if yaml_path.exists():
                for line in yaml_path.read_text(encoding="utf-8").splitlines():
                    m = _re.match(r"^\s*tmdb_api_key\s*:\s*[\"']?([^\"'\s#]+)[\"']?", line)
                    if m:
                        tmdb_key = m.group(1)
                    m = _re.match(r"^\s*omdb_api_key\s*:\s*[\"']?([^\"'\s#]+)[\"']?", line)
                    if m:
                        omdb_key = m.group(1)
                    m = _re.match(r"^\s*simkl_client_id\s*:\s*[\"']?([^\"'\s#]+)[\"']?", line)
                    if m:
                        simkl_key = m.group(1)

            imdb_ok = True  # api.tiffara.com is free with no key required

            parts = [
                f"IMDBApi:{1 if imdb_ok else 0}",
                f"TMDB:{1 if tmdb_key else 0}",
                f"OMDb:{1 if omdb_key else 0}",
                f"SIMKL:{1 if simkl_key else 0}",
            ]
            self.result_ready.emit(",".join(parts))
        except Exception:
            self.result_ready.emit("")


class InstallWorker(QThread):
    log_line  = pyqtSignal(str)
    step_done = pyqtSignal(str, str)   # key, state
    progress  = pyqtSignal(float, str)
    finished  = pyqtSignal(bool, str)  # success, message

    def __init__(self, install_dir: Path):
        super().__init__()
        self.install_dir = install_dir

    def _log(self, msg):
        self.log_line.emit(msg)

    def _step(self, key, state):
        self.step_done.emit(key, state)

    def _require_git(self) -> bool:
        """Ensure git is available, installing it via winget if needed."""
        if shutil.which("git"):
            return True

        self._log("git not found — attempting to install via winget...")
        # winget is built into Windows 10 1809+ and all Windows 11 machines
        if not shutil.which("winget"):
            return False

        for l in _run_cmd([
            "winget", "install", "--id", "Git.Git",
            "-e", "--source", "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]):
            self._log(l)

        # After winget install, git may not be on PATH in this process yet.
        # Find it manually in the standard location.
        git_default = Path("C:/Program Files/Git/cmd/git.exe")
        if git_default.exists():
            # Add to PATH for this process so subsequent calls work
            os.environ["PATH"] = str(git_default.parent) + os.pathsep + os.environ.get("PATH", "")
            self._log(f"git installed at {git_default}")
            return True

        return bool(shutil.which("git"))

    def _git(self, args: list, cwd=None):
        """Run a git command, using full path if needed."""
        git_exe = shutil.which("git") or "C:/Program Files/Git/cmd/git.exe"
        for l in _run_cmd([git_exe] + args, cwd=cwd):
            self._log(l)

    def run(self):
        try:
            d = self.install_dir
            self.progress.emit(0, "Starting…")

            # ── Step 1: verify bundled EnvyCore ───────────────────────────
            self._step("git", "active")
            self._log("── STEP 1: Verifying bundled EnvyCore")
            if not (d / "packages" / "envied").exists():
                raise RuntimeError(
                    "EnvyCore is missing or incomplete. "
                    "Please re-download EnvyUI from GitHub and try again."
                )
            self._log(f"EnvyCore found at: {d}")
            self._step("git", "done")
            self.progress.emit(0.2, "EnvyCore verified.")

            # ── Step 2: media tools via PS1 ────────────────────────────────
            self._step("tools", "active")
            self._step("tools", "active")
            self._log("── STEP 2: Media tools")

            tools_bin = Path("C:/Tools/bin")
            tools_bin.mkdir(parents=True, exist_ok=True)

            # ── 2a. Download media tools directly with progress reporting ────────
            # Previously used Install-media-tools.ps1 but it gives no progress
            # output during the long FFmpeg download, making users think it crashed.
            # Now we download everything ourselves with per-MB progress logging.
            import urllib.request as _urlreq, zipfile as _zf, tempfile as _tf2

            mkv_dir = Path("C:/Program Files/MKVToolNix")

            def _download_to_bin(url: str, dest_name: str, zip_match: str = None):
                """Download url with progress; if zip extract the file matching zip_match."""
                if (tools_bin / dest_name).exists():
                    self._log(f"{dest_name} already present — skipped.")
                    return
                self._log(f"Downloading {dest_name}...")
                try:
                    tmp = _tf2.NamedTemporaryFile(delete=False,
                        suffix=".zip" if zip_match else ".exe")
                    tmp.close()

                    _last_pct = [-1]
                    def _progress(block_num, block_size, total_size):
                        if total_size > 0:
                            pct = min(int(block_num * block_size * 100 / total_size), 100)
                            if pct != _last_pct[0] and pct % 5 == 0:
                                mb_done = block_num * block_size / 1024 / 1024
                                mb_total = total_size / 1024 / 1024
                                self._log(f"  {dest_name}: {pct}% ({mb_done:.1f} / {mb_total:.1f} MB)")
                                _last_pct[0] = pct
                        else:
                            mb_done = block_num * block_size / 1024 / 1024
                            if int(mb_done) != _last_pct[0]:
                                self._log(f"  {dest_name}: {mb_done:.1f} MB downloaded...")
                                _last_pct[0] = int(mb_done)

                    _urlreq.urlretrieve(url, tmp.name, reporthook=_progress)
                    self._log(f"  {dest_name}: download complete")
                    if zip_match:
                        with _zf.ZipFile(tmp.name) as z:
                            for member in z.namelist():
                                if member.lower().endswith(zip_match.lower()):
                                    data = z.read(member)
                                    (tools_bin / dest_name).write_bytes(data)
                                    self._log(f"Installed {dest_name} to {tools_bin}")
                                    break
                            else:
                                self._log(f"WARNING: {zip_match} not found in zip")
                    else:
                        import shutil as _sh2
                        _sh2.move(tmp.name, str(tools_bin / dest_name))
                        self._log(f"Installed {dest_name} to {tools_bin}")
                    try:
                        import os as _os3; _os3.unlink(tmp.name)
                    except Exception:
                        pass
                except Exception as e:
                    self._log(f"WARNING: Could not download {dest_name}: {e}")

            # ── Download FFmpeg if not present ───────────────────────────────
            if (tools_bin / "ffmpeg.exe").exists():
                self._log("ffmpeg.exe already present — skipped.")
            else:
                self._log("Fetching latest FFmpeg release URL...")
                try:
                    import json as _json
                    import urllib.request as _req2
                    # Get latest release from GitHub API
                    api_url = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
                    with _req2.urlopen(api_url, timeout=15) as _r:
                        _rel = _json.loads(_r.read())
                    _ffmpeg_url = next(
                        (a["browser_download_url"] for a in _rel.get("assets", [])
                         if a["name"].endswith("full_build.zip")),
                        None
                    )
                    if not _ffmpeg_url:
                        raise ValueError("Could not find FFmpeg full_build.zip in release")
                    self._log(f"Downloading FFmpeg: {_ffmpeg_url.split('/')[-1]}")
                    # Download zip with progress
                    _tmp_ffmpeg = _tf2.NamedTemporaryFile(delete=False, suffix=".zip")
                    _tmp_ffmpeg.close()
                    _ffmpeg_last = [-1]
                    def _ffmpeg_progress(block_num, block_size, total_size):
                        if total_size > 0:
                            pct = min(int(block_num * block_size * 100 / total_size), 100)
                            if pct != _ffmpeg_last[0] and pct % 5 == 0:
                                mb_done = block_num * block_size / 1024 / 1024
                                mb_total = total_size / 1024 / 1024
                                self._log(f"  ffmpeg: {pct}% ({mb_done:.0f} / {mb_total:.0f} MB)")
                                _ffmpeg_last[0] = pct
                    _urlreq.urlretrieve(_ffmpeg_url, _tmp_ffmpeg.name, reporthook=_ffmpeg_progress)
                    self._log("  ffmpeg: download complete — extracting...")
                    # Extract ffmpeg.exe, ffprobe.exe from zip
                    with _zf.ZipFile(_tmp_ffmpeg.name) as _zff:
                        for _member in _zff.namelist():
                            _bn = _member.split("/")[-1].lower()
                            if _bn in ("ffmpeg.exe", "ffprobe.exe"):
                                _data = _zff.read(_member)
                                (tools_bin / _bn).write_bytes(_data)
                                self._log(f"  Installed {_bn} to {tools_bin}")
                    try:
                        import os as _os4; _os4.unlink(_tmp_ffmpeg.name)
                    except Exception:
                        pass
                except Exception as _fe:
                    self._log(f"WARNING: FFmpeg download failed: {_fe}")

            # ── Install Bento4 (mp4decrypt) via winget ───────────────────────
            if (tools_bin / "mp4decrypt.exe").exists():
                self._log("mp4decrypt.exe already present — skipped.")
            else:
                self._log("Installing Bento4 via winget...")
                try:
                    _b4_result = []
                    for _l in _run_cmd(
                        ["winget", "install", "--id", "AxiomaticSystems.Bento4",
                         "--silent", "--accept-package-agreements",
                         "--accept-source-agreements"],
                    ):
                        self._log(f"  {_l}")
                        _b4_result.append(_l)
                    # Copy mp4decrypt.exe to tools_bin - winget installs to LocalAppData
                    import glob as _glob, shutil as _sh3
                    _b4_search = [
                        str(Path.home() / "AppData/Local/Microsoft/WinGet/Packages/AxiomaticSystems*/**/mp4decrypt.exe"),
                        str(Path.home() / "AppData/Local/Programs/Bento4*/**/mp4decrypt.exe"),
                        "C:/Program Files/Bento4*/**/mp4decrypt.exe",
                    ]
                    _b4_paths = []
                    for _pat in _b4_search:
                        _b4_paths += _glob.glob(_pat, recursive=True)
                    if _b4_paths:
                        _sh3.copy2(_b4_paths[0], str(tools_bin / "mp4decrypt.exe"))
                        self._log(f"  Copied mp4decrypt.exe to {tools_bin}")
                    elif not (tools_bin / "mp4decrypt.exe").exists():
                        self._log("WARNING: mp4decrypt.exe not found after winget install")
                except Exception as _b4e:
                    self._log(f"WARNING: Bento4 winget install failed: {_b4e}")

            # ── MKVToolNix handled below with portable zip ────────────────────
            # (skipped here — done after other tools using portable zip)

            # ── N_m3u8DL-RE — always ensure this is present ──────────────────
            _download_to_bin(
                "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/"
                "v0.3.0-beta/N_m3u8DL-RE_v0.3.0-beta_win-x64_20241203.zip",
                "N_m3u8DL-RE.exe",
                "N_m3u8DL-RE.exe"
            )
            # dovi_tool — also after the abort point
            _download_to_bin(
                "https://github.com/quietvoid/dovi_tool/releases/download/"
                "2.3.1/dovi_tool-2.3.1-x86_64-pc-windows-msvc.zip",
                "dovi_tool.exe",
                "dovi_tool.exe"
            )
            # hdr10plus_tool — also after the abort point
            _download_to_bin(
                "https://github.com/quietvoid/hdr10plus_tool/releases/download/"
                "1.7.1/hdr10plus_tool-1.7.1-x86_64-pc-windows-msvc.zip",
                "hdr10plus_tool.exe",
                "hdr10plus_tool.exe"
            )
            # shaka-packager — required for DASH decryption (ITV, Disney+, etc.)
            # PS1 downloads this as a plain .exe (not a zip)
            _download_to_bin(
                "https://github.com/shaka-project/shaka-packager/releases/download/"
                "v2.6.1/packager-win-x64.exe",
                "shaka-packager.exe",
                None   # not a zip — direct .exe download
            )
            # ── CCExtractor — closed-caption extraction ───────────────────────
            if (tools_bin / "ccextractor.exe").exists():
                self._log("ccextractor.exe already present — skipped.")
            else:
                self._log("Downloading CCExtractor...")
                try:
                    import json as _ccjson, tempfile as _cctf, zipfile as _cczf
                    with _urlreq.urlopen(
                        _urlreq.Request(
                            "https://api.github.com/repos/CCExtractor/ccextractor/releases/latest",
                            headers={"User-Agent": "EnvyUI-Installer"},
                        ), timeout=20
                    ) as _ccr:
                        _cc_rel = _ccjson.loads(_ccr.read())
                    _cc_url = next(
                        (a["browser_download_url"] for a in _cc_rel.get("assets", [])
                         if "win" in a["name"].lower() and a["name"].endswith(".zip")),
                        None,
                    )
                    if _cc_url:
                        _cc_tmp = _cctf.NamedTemporaryFile(delete=False, suffix=".zip")
                        _cc_tmp.close()
                        _cc_last = [-1]
                        def _cc_progress(block_num, block_size, total_size):
                            if total_size > 0:
                                pct = min(int(block_num * block_size * 100 / total_size), 100)
                                if pct != _cc_last[0] and pct % 10 == 0:
                                    self._log(f"  ccextractor.exe: {pct}%")
                                    _cc_last[0] = pct
                        _urlreq.urlretrieve(_cc_url, _cc_tmp.name, reporthook=_cc_progress)
                        self._log("  ccextractor: download complete — extracting...")
                        with _cczf.ZipFile(_cc_tmp.name) as _ccz:
                            # Extract all files flat into tools_bin (skip subdirs)
                            for member in _ccz.namelist():
                                fname = Path(member).name
                                if not fname:
                                    continue
                                data = _ccz.read(member)
                                (tools_bin / fname).write_bytes(data)
                        self._log(f"CCExtractor installed to {tools_bin}")
                        try:
                            import os as _ccos; _ccos.unlink(_cc_tmp.name)
                        except Exception:
                            pass
                    else:
                        self._log("WARNING: Could not find CCExtractor Windows zip in latest release")
                except Exception as _cce:
                    self._log(f"WARNING: Could not download CCExtractor: {_cce}")
            # ── Install MKVToolNix via winget (no admin, no download URL needed) ─
            mkv_dir = tools_bin
            if (tools_bin / "mkvmerge.exe").exists():
                self._log("mkvmerge.exe already present — skipped.")
            else:
                self._log("Installing MKVToolNix via winget...")
                try:
                    for _l in _run_cmd(
                        ["winget", "install", "--id", "MoritzBunkus.MKVToolNix",
                         "--silent", "--accept-package-agreements",
                         "--accept-source-agreements"],
                    ):
                        self._log(f"  {_l}")
                    # Copy key exes to tools_bin from default install location
                    import glob as _glob2, shutil as _sh4
                    _mkv_search = [
                        "C:/Program Files/MKVToolNix/mkvmerge.exe",
                        str(Path.home() / "AppData/Local/Programs/MKVToolNix/mkvmerge.exe"),
                        str(Path.home() / "AppData/Local/Microsoft/WinGet/Packages/MoritzBunkus*/**/mkvmerge.exe"),
                    ]
                    _mkv_installed = []
                    for _pat in _mkv_search:
                        _mkv_installed += _glob2.glob(_pat, recursive=True)
                    if _mkv_installed:
                        _mkv_install_dir = Path(_mkv_installed[0]).parent
                        _mkv_skip = {"uninst.exe", "uninstall.exe"}
                        for _mkv_exe in _mkv_install_dir.glob("*.exe"):
                            if _mkv_exe.name.lower() not in _mkv_skip:
                                _sh4.copy2(str(_mkv_exe), str(tools_bin / _mkv_exe.name))
                        self._log(f"  Copied MKVToolNix exes to {tools_bin}")
                    elif not (tools_bin / "mkvmerge.exe").exists():
                        self._log("WARNING: mkvmerge.exe not found after winget install")
                except Exception as _me3:
                    self._log(f"WARNING: MKVToolNix winget install failed: {_me3}")
            # SubtitleEdit — portable zip, goes to C:\Tools\SubtitleEdit
            se_dir = Path("C:/Tools/SubtitleEdit")
            se_exe = se_dir / "SubtitleEdit.exe"
            if se_exe.exists():
                self._log("SubtitleEdit already installed — skipped.")
            else:
                self._log("Downloading SubtitleEdit...")
                se_dir.mkdir(parents=True, exist_ok=True)
                import tempfile as _tf_se, zipfile as _zf_se
                se_tmp = _tf_se.NamedTemporaryFile(
                    delete=False, suffix=".zip", prefix="twinvine_se_"
                )
                se_tmp.close()
                try:
                    _urlreq.urlretrieve(
                        "https://github.com/SubtitleEdit/subtitleedit/releases/"
                        "download/4.0.14/SE4014.zip",
                        se_tmp.name
                    )
                    with _zf_se.ZipFile(se_tmp.name) as z:
                        z.extractall(str(se_dir))
                    # Create a launcher .cmd in C:\Tools\bin for PATH access
                    launcher_cmd = tools_bin / "SubtitleEdit.cmd"
                    launcher_cmd.write_text(
                        '@echo off\n"C:\\Tools\\SubtitleEdit\\SubtitleEdit.exe" %*\n'
                    )
                    if se_exe.exists():
                        self._log(f"SubtitleEdit installed to {se_dir}")
                    else:
                        self._log("WARNING: SubtitleEdit zip extracted but .exe not found")
                except Exception as e:
                    self._log(f"WARNING: Could not install SubtitleEdit: {e}")
                finally:
                    try:
                        import os as _os_se; _os_se.unlink(se_tmp.name)
                    except Exception:
                        pass

            # ── 2c. Add tool dirs to User PATH (no admin needed) ──────────────
            import tempfile as _tf3, os as _os4
            user_path_script = (
                "$toolDirs = @('C:\\Tools\\bin', 'C:\\Program Files\\MKVToolNix')\r\n"
                "$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')\r\n"
                "foreach ($dir in $toolDirs) {\r\n"
                "    if (-not $userPath.Contains($dir)) {\r\n"
                "        $userPath = $dir + ';' + $userPath\r\n"
                "    }\r\n"
                "}\r\n"
                "[Environment]::SetEnvironmentVariable('Path', $userPath, 'User')\r\n"
                "Write-Host 'Tool paths added to User PATH.'\r\n"
            )
            tmp_path = _tf3.NamedTemporaryFile(
                mode='w', suffix='.ps1', delete=False,
                encoding='utf-8', prefix='twinvine_up_'
            )
            tmp_path.write(user_path_script)
            tmp_path.close()
            for l in _run_cmd(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", tmp_path.name],
                cwd=d
            ): self._log(l)
            try:
                _os4.unlink(tmp_path.name)
            except Exception:
                pass

            # Log what's now in C:\Tools\bin
            found = [p.name for p in tools_bin.glob("*.exe")] if tools_bin.exists() else []
            self._log(f"C:\\Tools\\bin contents: {found}")

            self._step("tools", "done")
            self.progress.emit(0.50, "Media tools installed.")

            # ── Step 3: uv ─────────────────────────────────────────────────
            self._step("uv", "active")
            self._log("── STEP 3: uv package manager")

            def find_uv() -> str | None:
                """Search all likely locations for uv.exe — never assumes PATH."""
                search_dirs = []

                # 1. Saved path from a previous install (most reliable)
                try:
                    saved = load_config().get("uv_exe", "")
                    if saved and Path(saved).exists():
                        return saved
                except Exception:
                    pass

                # 2. sys.executable and related dirs
                # When running under venv Python, sys.base_prefix points to the
                # real system Python — uv was pip-installed there, not in the venv.
                for prefix in {sys.prefix, sys.base_prefix,
                               str(Path(sys.executable).parent.parent)}:
                    search_dirs += [
                        Path(prefix) / "Scripts" / "uv.exe",
                        Path(prefix) / "uv.exe",
                    ]

                # 3. Common uv install locations
                appdata = os.environ.get("APPDATA", "")
                localappdata = os.environ.get("LOCALAPPDATA", "")
                home = Path(os.path.expanduser("~"))
                search_dirs += [
                    Path(appdata) / "uv" / "bin" / "uv.exe",
                    Path(localappdata) / "uv" / "bin" / "uv.exe",
                    home / ".cargo" / "bin" / "uv.exe",
                    home / ".local" / "bin" / "uv.exe",
                ]

                for c in search_dirs:
                    if c.exists():
                        return str(c)

                # 4. Every dir on the current PATH
                for p in os.environ.get("PATH", "").split(os.pathsep):
                    c = Path(p) / "uv.exe"
                    if c.exists():
                        return str(c)

                # 5. shutil.which (may miss non-PATH locations but worth trying)
                return shutil.which("uv")

            uv_exe = find_uv()
            if uv_exe:
                self._log(f"uv already available at: {uv_exe}")
            else:
                # Install uv using the SYSTEM Python (not the venv Python which
                # has no pip). Find the system Python next to uv's expected location.
                self._log("uv not found — installing...")
                # Try PowerShell installer (doesn't need pip at all)
                import tempfile as _tf_uv
                uv_ps = (
                    "irm https://github.com/astral-sh/uv/releases/latest/download/"
                    "uv-installer.ps1 | iex"
                )
                for l in _run_cmd(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-Command", uv_ps]
                ): self._log(l)

                uv_exe = find_uv()
                if not uv_exe:
                    # Last resort: pip on the system Python
                    # Walk up from sys.executable to find a Python with pip
                    for py_candidate in [
                        sys.executable,
                        str(Path(sys.base_prefix) / "python.exe"),
                        "python",
                    ]:
                        try:
                            import subprocess as _sp
                            result = _sp.run(
                                [py_candidate, "-m", "pip", "install", "uv"],
                                capture_output=True, text=True
                            )
                            self._log(result.stdout)
                            uv_exe = find_uv()
                            if uv_exe:
                                break
                        except Exception:
                            continue

            if not uv_exe:
                raise RuntimeError(
                    "uv could not be found or installed. "
                    "Please install manually: pip install uv"
                )
            # Make sure uv's directory is on PATH for subprocesses
            uv_dir = str(Path(uv_exe).parent)
            if uv_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = uv_dir + os.pathsep + os.environ.get("PATH", "")
            self._step("uv", "done")
            self.progress.emit(0.65, "uv ready.")

            # ── Step 4: uv lock + sync ─────────────────────────────────────
            self._step("sync", "active")
            self._log("── STEP 4: uv lock + uv sync")
            # Find the system Python (not venv python, not pythonw — uv needs
            # the real python.exe to pin the venv to the correct version,
            # preventing a version mismatch relaunch that causes terminal windows)
            import shutil as _sh2
            sys_python = None
            # Walk PATH to find a python.exe that isn't inside our venv
            for candidate in _sh2.which("python", mode=os.F_OK) and [_sh2.which("python")] or []:
                if candidate and ".venv" not in candidate and str(d) not in candidate:
                    try:
                        if Path(candidate).stat().st_size > 0:
                            sys_python = candidate
                            break
                    except Exception:
                        pass
            # Fallback: use sys.executable if it's not the venv python
            if not sys_python:
                if ".venv" not in sys.executable and str(d) not in sys.executable:
                    sys_python = sys.executable
            # Write a .python-version file so uv uses exactly the system Python.
            # This is the correct uv mechanism — it pins the venv to the exact
            # version string, preventing uv from downloading cpython-3.13.0
            # when 3.13.13 is installed, which would cause a mismatch relaunch
            # and a terminal window on every subsequent launch.
            # Patch pyproject.toml to accept the system Python version.
            # TwinVine requires <=3.13 but 3.13.13 > 3.13 in semver.
            # We widen it to <=3.99 so any current Python works without
            # uv downloading its own cached Python.
            # Also delete any stale .python-version file which causes uv
            # to ignore --python and pick its cached version instead.
            for stale in [d / ".python-version"]:
                try:
                    if stale.exists():
                        stale.unlink()
                        self._log(f"Removed stale: {stale.name}")
                except Exception:
                    pass

            # Patch requires-python in all workspace pyproject.toml files
            # Backs up original before patching
            import re as _re
            for toml_path in list(d.rglob("pyproject.toml")):
                try:
                    text = toml_path.read_text(encoding="utf-8")
                    # Match any upper bound <=3.x that might block newer Python
                    import re as _re_toml
                    _patched = _re_toml.sub(
                        r'(requires-python\s*=\s*">=3\.\d+,\s*<=3\.)(\d+)(")',
                        r'\g<1>99\3',
                        text
                    )
                    if _patched != text:
                        bak = toml_path.with_suffix(".toml.bak")
                        if not bak.exists():
                            bak.write_text(text, encoding="utf-8")
                        toml_path.write_text(_patched, encoding="utf-8")
                        self._log(f"Patched requires-python in {toml_path.name} (backup: {bak.name})")
                except Exception as e:
                    self._log(f"Note: could not patch {toml_path}: {e}")

            # Skip pyproject.toml patch if venv is already functional
            _venv_python = d / ".venv" / "Scripts" / "python.exe"
            _venv_working = False
            if _venv_python.exists():
                try:
                    import subprocess as _sp_check
                    _r = _sp_check.run(
                        [str(_venv_python), "-c", "import envied"],
                        capture_output=True, timeout=15,
                        creationflags=_sp_check.CREATE_NO_WINDOW
                    )
                    _venv_working = (_r.returncode == 0)
                except Exception:
                    pass

            if _venv_working:
                self._log("Existing venv is functional — skipping pyproject.toml patch.")
            else:
                # Patch requires-python only if venv isn't working yet
                import re as _re2
                for toml_path in list(d.rglob("pyproject.toml")):
                    try:
                        text = toml_path.read_text(encoding="utf-8")
                        import re as _re_toml2
                        _patched2 = _re_toml2.sub(
                            r'(requires-python\s*=\s*">=3\.\d+,\s*<=3\.)(\d+)(")',
                            r'\g<1>99\3',
                            text
                        )
                        if _patched2 != text:
                            bak = toml_path.with_suffix(".toml.bak")
                            if not bak.exists():
                                bak.write_text(text, encoding="utf-8")
                            toml_path.write_text(_patched2, encoding="utf-8")
                            self._log(f"Patched requires-python in {toml_path.name}")
                    except Exception as e:
                        self._log(f"Note: could not patch {toml_path}: {e}")

            # Use system python.exe directly so uv doesn't download its own
            sys_py = sys.executable
            if ".venv" in sys_py or str(d) in sys_py:
                import shutil as _sh3
                sys_py = _sh3.which("python") or sys.executable
            self._log(f"uv will use: {sys_py}")

            # uv.lock is bundled with EnvyCore — no deletion needed.
            # ── Patch utilities.py FPS class for Python 3.14 compatibility ──────
            # ast.Num was removed in Python 3.14 — add visit_Constant as replacement
            _utils_path = d / "packages/envied/src/envied/core/utilities.py"
            if _utils_path.exists():
                try:
                    _utils_txt = _utils_path.read_text(encoding="utf-8")
                    if "def visit_Num" in _utils_txt and "def visit_Constant" not in _utils_txt:
                        _utils_bak = _utils_path.with_name("utilities.py.bak")
                        if not _utils_bak.exists():
                            _utils_bak.write_text(_utils_txt, encoding="utf-8")
                        _old_method = "    def visit_Num(self, node: ast.Num) -> complex:\n        return node.n"
                        _new_method = _old_method + "\n\n    def visit_Constant(self, node: ast.Constant) -> complex:\n        return node.value"
                        _utils_txt = _utils_txt.replace(_old_method, _new_method)
                        _utils_path.write_text(_utils_txt, encoding="utf-8")
                        self._log("Patched utilities.py: added visit_Constant for Python 3.14")
                except Exception as _ue:
                    self._log(f"Note: could not patch utilities.py: {_ue}")

            # ── Pre-patch: relax pywinpty upper bound so uv can resolve 3.0.5
            # envied pins pywinpty>=2.0.0,<3 but 2.x has no Python 3.14 wheel.
            # pywinpty 3.0.5 added Python 3.14 support — widen the bound to allow it.
            for _toml in list(d.rglob("pyproject.toml")):
                try:
                    _txt = _toml.read_text(encoding="utf-8")
                    _new_txt = _txt.replace(
                        '"pywinpty>=2.0.0,<3"', '"pywinpty>=2.0.0"'
                    ).replace(
                        "'pywinpty>=2.0.0,<3'", "'pywinpty>=2.0.0'"
                    )
                    if _new_txt != _txt:
                        _toml.write_text(_new_txt, encoding="utf-8")
                        self._log(f"Patched pywinpty version bound in {_toml.name}")
                except Exception as _pe:
                    self._log(f"Note: could not patch pywinpty in {_toml}: {_pe}")

            # ── Pre-patch: replace brotli with brotlicffi in pyproject.toml files
            # brotli requires C++ Build Tools on Python 3.14+ — brotlicffi is pure Python
            _brotli_patched = False
            for _toml in list(d.rglob("pyproject.toml")):
                try:
                    _txt = _toml.read_text(encoding="utf-8")
                    # Replace all brotli dependency entries (with or without version specifier)
                    _new_txt = _txt
                    for _old, _new in [
                        ('"brotli"', '"brotlicffi"'),
                        ("'brotli'", "'brotlicffi'"),
                        ('"brotli>=', '"brotlicffi>='),
                        ('"brotli==', '"brotlicffi=='),
                        ('"brotli<', '"brotlicffi<'),
                        ("'brotli>=", "'brotlicffi>="),
                        ("'brotli==", "'brotlicffi=="),
                    ]:
                        _new_txt = _new_txt.replace(_old, _new)
                    if _new_txt != _txt:
                        _bak = _toml.with_name(_toml.stem + "_brotli.toml.bak")
                        if not _bak.exists():
                            _bak.write_text(_txt, encoding="utf-8")
                        _toml.write_text(_new_txt, encoding="utf-8")
                        self._log(f"Patched brotli to brotlicffi in {_toml.name}")
                        _brotli_patched = True
                except Exception as _be:
                    self._log(f"Note: could not patch brotli in {_toml}: {_be}")

            # uv.lock already deleted above before patching

            # ── Git required for subby (git dependency in uv.lock) ────────────
            if not self._require_git():
                raise RuntimeError(
                    "Git could not be installed. "
                    "Please install Git for Windows from https://git-scm.com and try again."
                )
            self._log("Git available — proceeding with uv sync.")

            for l in _run_cmd([uv_exe, "lock"], cwd=d): self._log(l)
            _pywinpty_fail = [False]
            _skip_until_hint = [False]
            _sync_failed = [False]
            for l in _run_cmd([uv_exe, "sync", "--python", sys_py], cwd=d):
                # Suppress the pywinpty build-from-source failure block —
                # we handle it ourselves with a binary wheel install below.
                if 'Building pywinpty' in l:
                    _skip_until_hint[0] = True
                if _skip_until_hint[0]:
                    if 'Failed to build' in l or 'pywinpty' in l.lower():
                        _pywinpty_fail[0] = True
                    # Stop suppressing once past the hint lines
                    if l.startswith('hint:') and 'pywinpty' not in l:
                        _skip_until_hint[0] = False
                    continue
                if 'Failed to download' in l or 'Git operation failed' in l or ('error' in l.lower() and 'failed' in l.lower()):
                    _sync_failed[0] = True
                self._log(l)

            if _sync_failed[0]:
                raise RuntimeError(
                    "uv sync failed — one or more packages could not be installed. "
                    "Check the log above for details."
                )

            self._step("sync", "done")
            self.progress.emit(0.85, "Packages synced.")

            # ── Download xterm.js locally so the terminal works offline ────
            _xterm_dir = d / "assets" / "xterm"
            _xterm_dir.mkdir(parents=True, exist_ok=True)
            _xterm_js  = _xterm_dir / "xterm.min.js"
            _xterm_css = _xterm_dir / "xterm.min.css"
            if not _xterm_js.exists() or not _xterm_css.exists():
                self._log("Downloading xterm.js (terminal renderer)...")
                import urllib.request as _ur
                _base = "https://cdn.jsdelivr.net/npm/xterm@5.3.0"
                try:
                    _ur.urlretrieve(f"{_base}/lib/xterm.min.js",  str(_xterm_js))
                    _ur.urlretrieve(f"{_base}/css/xterm.min.css", str(_xterm_css))
                    self._log("xterm.js downloaded.")
                except Exception as _xe:
                    self._log(f"Note: could not download xterm.js: {_xe}")
            else:
                self._log("xterm.js already present — skipped.")

            # ── Step 5: YAML config ────────────────────────────────────────
            self._step("yaml", "active")
            src = d / "packages/envied/src/envied/envied-working-example.yaml"
            dst = d / "packages/envied/src/envied/envied.yaml"
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
                self._log("Copied envied example YAML.")
            else:
                self._log("envied.yaml already present — skipped.")

            # ── Patch vaults + cookies paths to absolute so they work from any cwd ──
            vaults_abs  = d / "packages/envied/src/envied/vaults"
            cookies_abs = d / "Cookies"
            cookies_abs.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                try:
                    import re as _re
                    yaml_text = dst.read_text(encoding="utf-8")

                    def _patch_dir(text: str, key: str, abs_path: Path) -> tuple[str, str]:
                        """Replace a relative `key: value` line with the absolute path. Returns (new_text, status)."""
                        target = abs_path.as_posix() + ("/" if key == "vaults" else "")
                        pattern = _re.compile(r'^([ \t]*' + key + r':[ \t]*)(\S.+?)[ \t]*$', _re.MULTILINE)
                        m = pattern.search(text)
                        if not m:
                            return text, f"Warning: {key} entry not found in envied.yaml"
                        if m.group(2).strip() == target.strip():
                            return text, f"{key} path already correct — skipped."
                        bak_yaml = dst.with_suffix(".yaml.bak")
                        if not bak_yaml.exists():
                            bak_yaml.write_text(text, encoding="utf-8")
                        text = pattern.sub(lambda _m: f"{_m.group(1)}{target}", text, count=1)
                        return text, f"Patched {key} path to: {target}"

                    yaml_text, status = _patch_dir(yaml_text, "vaults", vaults_abs)
                    self._log(status)
                    yaml_text, status = _patch_dir(yaml_text, "cookies", cookies_abs)
                    self._log(status)

                    # ── Patch output_template folder block (added v1.0.5) ──────
                    # Existing installs won't have the folder: subsection — insert
                    # it after the songs: line if it isn't already present.
                    if "folder:" not in yaml_text and "output_template:" in yaml_text:
                        _folder_block = (
                            "  folder:\n"
                            "    movies: '{title} ({year})'\n"
                            "    series: '{title} ({year?})'\n"
                        )
                        _songs_pat = _re.compile(
                            r"^([ \t]*songs:[ \t]*['\"].+?['\"][ \t]*)$",
                            _re.MULTILINE,
                        )
                        _m = _songs_pat.search(yaml_text)
                        if _m:
                            insert_at = _m.end()
                            yaml_text = yaml_text[:insert_at] + "\n" + _folder_block + yaml_text[insert_at:]
                            self._log("Patched envied.yaml: added output_template folder block.")
                        else:
                            self._log("Warning: could not find songs: line to insert folder block.")

                    dst.write_text(yaml_text, encoding="utf-8")
                except Exception as _e:
                    self._log(f"Warning: could not patch yaml paths: {_e}")

            # ── Patch dl.py: shorten per-episode summary line ─────────────────
            # "Processed all titles in Xm Xs" appears after every single episode
            # which misleads when downloading multiple episodes.  Change to the
            # shorter "Processed in Xm Xs" so it reads correctly either way.
            _dl_py = d / "packages/envied/src/envied/commands/dl.py"
            _OLD_DL = 'f"Processed all titles in [progress.elapsed]{dl_time}"'
            _NEW_DL = 'f"Processed in [progress.elapsed]{dl_time}"'
            try:
                if _dl_py.exists():
                    _dl_txt = _dl_py.read_text(encoding="utf-8")
                    if _OLD_DL in _dl_txt:
                        _dl_bak = _dl_py.with_suffix(".py.bak")
                        if not _dl_bak.exists():
                            _dl_bak.write_text(_dl_txt, encoding="utf-8")
                        _dl_py.write_text(_dl_txt.replace(_OLD_DL, _NEW_DL), encoding="utf-8")
                        self._log("Patched dl.py: shortened per-episode summary line.")
                    else:
                        self._log("dl.py already patched or text not found — skipped.")
            except Exception as _de:
                self._log(f"Note: could not patch dl.py: {_de}")

            # ── Patch TEN/__init__.py ─────────────────────────────────────────
            # 1. Remove broken config.downloader check (dropped in TwinVine 5.3)
            # 2. Force title.language="en" in get_tracks (Episode constructors
            #    may not persist language due to pycache issues on some installs)
            # 3. Guard OnSegmentFilter to skip ad segments correctly
            _ten_init = d / "packages/envied/src/envied/services/TEN/__init__.py"
            try:
                if _ten_init.exists():
                    _ten_txt = _ten_init.read_text(encoding="utf-8")
                    _changed = False

                    # Fix 1: remove config.downloader gate
                    _old_dl_check = (
                        'if config.downloader != "n_m3u8dl_re":\n'
                        '            self.log.error(" - Error: n_m3u8dl_re downloader is required for this service.")\n'
                        '            sys.exit(1)\n'
                    )
                    if _old_dl_check in _ten_txt:
                        _ten_txt = _ten_txt.replace(_old_dl_check, "")
                        _changed = True

                    # Fix 2: language guard in get_tracks
                    _old_get_tracks = "    def get_tracks(self, title: Union[Movie, Episode]) -> Tracks:\n        playback_url"
                    _new_get_tracks = (
                        "    def get_tracks(self, title: Union[Movie, Episode]) -> Tracks:\n"
                        "        if not title.language:\n"
                        "            title.language = Language.get(\"en\")\n"
                        "        playback_url"
                    )
                    if _old_get_tracks in _ten_txt and "if not title.language" not in _ten_txt:
                        _ten_txt = _ten_txt.replace(_old_get_tracks, _new_get_tracks)
                        _changed = True

                    # Fix 3: OnSegmentFilter for DAI ad segments
                    _old_filter = (
                        "        for track in tracks:\n"
                        "            if isinstance(track, Subtitle):\n"
                        "                track.downloader = requests"
                    )
                    _new_filter = (
                        "        for track in tracks:\n"
                        "            track.OnSegmentFilter = lambda x: re.search(r\"redirector\\.googlevideo\\.com\", x.uri)\n"
                        "            if isinstance(track, Subtitle):\n"
                        "                track.downloader = requests"
                    )
                    if _old_filter in _ten_txt and "OnSegmentFilter" not in _ten_txt:
                        _ten_txt = _ten_txt.replace(_old_filter, _new_filter)
                        _changed = True

                    if _changed:
                        _ten_bak = _ten_init.with_suffix(".py.bak")
                        if not _ten_bak.exists():
                            _ten_bak.write_text(_ten_init.read_text(encoding="utf-8"), encoding="utf-8")
                        _ten_init.write_text(_ten_txt, encoding="utf-8")
                        self._log("Patched TEN/__init__.py: removed broken checks, added language fix and ad filter.")
                    else:
                        self._log("TEN/__init__.py already patched — skipped.")
            except Exception as _te:
                self._log(f"Note: could not patch TEN/__init__.py: {_te}")

            # ── Patch CWTV/__init__.py ────────────────────────────────────────
            # Episode constructors may not persist language, causing the core
            # track selector to fall back to "orig" and skip the video track.
            # Force language="en" at the top of get_tracks to prevent this.
            _cwtv_init = d / "packages/envied/src/envied/services/CWTV/__init__.py"
            try:
                if _cwtv_init.exists():
                    _cwtv_txt = _cwtv_init.read_text(encoding="utf-8")
                    _old_cwtv = (
                        "    def get_tracks(self, title: Movie | Episode) -> Tracks:\n"
                        "        data = self._request(\n"
                    )
                    _new_cwtv = (
                        "    def get_tracks(self, title: Movie | Episode) -> Tracks:\n"
                        "        if not title.language:\n"
                        "            from langcodes import Language\n"
                        "            title.language = Language.get(\"en\")\n"
                        "        data = self._request(\n"
                    )
                    if _old_cwtv in _cwtv_txt and "if not title.language" not in _cwtv_txt:
                        _cwtv_bak = _cwtv_init.with_suffix(".py.bak")
                        if not _cwtv_bak.exists():
                            _cwtv_bak.write_text(_cwtv_txt, encoding="utf-8")
                        _cwtv_init.write_text(_cwtv_txt.replace(_old_cwtv, _new_cwtv), encoding="utf-8")
                        self._log("Patched CWTV/__init__.py: added language guard in get_tracks.")
                    else:
                        self._log("CWTV/__init__.py already patched — skipped.")
            except Exception as _cwe:
                self._log(f"Note: could not patch CWTV/__init__.py: {_cwe}")

            # ── Patch hls.py: guard against zero-length decrypt batch ─────────
            # Google DAI streams have ad breaks with different AES keys.
            # When an ad batch is skipped, the key-change trigger can fire with
            # last_segment_i < first_segment_i (range_len <= 0).  Without this
            # guard the download crashes with "None of the segment files exist".
            _hls_py = d / "packages/envied/src/envied/core/manifests/hls.py"
            _OLD_HLS = "                range_len = (last_segment_i - first_segment_i) + 1\n\n                segment_range"
            _NEW_HLS = (
                "                range_len = (last_segment_i - first_segment_i) + 1\n\n"
                "                if range_len <= 0:\n"
                "                    return None  # empty batch at key-change boundary (e.g. skipped ad segments)\n\n"
                "                segment_range"
            )
            try:
                if _hls_py.exists():
                    _hls_txt = _hls_py.read_text(encoding="utf-8")
                    if _OLD_HLS in _hls_txt:
                        _hls_bak = _hls_py.with_suffix(".py.bak")
                        if not _hls_bak.exists():
                            _hls_bak.write_text(_hls_txt, encoding="utf-8")
                        _hls_py.write_text(_hls_txt.replace(_OLD_HLS, _NEW_HLS), encoding="utf-8")
                        self._log("Patched hls.py: added empty-batch guard for DAI ad-break key changes.")
                    else:
                        self._log("hls.py already patched or text not found — skipped.")
            except Exception as _he:
                self._log(f"Note: could not patch hls.py: {_he}")

            # ── Patch PBS/__init__.py: _fetch_video_bridge for Next.js ───────────
            # PBS migrated from portalplayer/window.videoBridge to Next.js App Router.
            # URS redirect URLs now live in RSC payload chunks (self.__next_f.push).
            # Find the chunk marked "embedType":"portalplayer" and take its URS URL.
            _pbs_init = d / "packages/envied/src/envied/services/PBS/__init__.py"
            _PBS_SENTINEL = "__next_f"   # present only in our patched version
            _PBS_NEW_BRIDGE = r"""    def _fetch_video_bridge(self, video_slug: str) -> dict:
        # PBS migrated to Next.js App Router — URS redirect URLs live in the RSC payload.
        # The main player component always contains both the URS URL and
        # "embedType":"portalplayer" in the same self.__next_f.push() chunk.
        r = self.session.get(f"https://www.pbs.org/video/{video_slug}/", timeout=15)
        r.raise_for_status()
        html = r.text

        URS_PAT = re.compile(r'https://urs\.pbs\.org/redirect/[A-Za-z0-9/]+')

        # Split page into RSC chunks and find the chunk containing "portalplayer" —
        # that chunk also holds the URS URL for the main episode player.
        main_urs = None
        for chunk in re.split(r'self\.__next_f\.push\(', html):
            if "portalplayer" not in chunk:
                continue
            found = [u.replace("\\/", "/") for u in URS_PAT.findall(chunk)]
            if found:
                main_urs = found[0]
                break

        # Fallback: take the first URS URL found anywhere in the page
        if not main_urs:
            all_urs = list(dict.fromkeys(
                u.replace("\\/", "/") for u in URS_PAT.findall(html)
            ))
            main_urs = all_urs[0] if all_urs else None

        if main_urs:
            title_str = video_slug.replace("-", " ").title()
            for script_m in re.finditer(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            ):
                try:
                    ld = json.loads(script_m.group(1))
                    blocks = ld if isinstance(ld, list) else [ld]
                    for block in blocks:
                        items = block.get("@graph", [block])
                        for item in items:
                            vo = item if item.get("@type") == "VideoObject" else item.get("video")
                            if isinstance(vo, dict) and vo.get("name"):
                                title_str = vo["name"]
                                break
                except Exception:
                    pass
            return {
                "availability": "available",
                "encodings": [main_urs],
                "program": {"slug": None, "title": title_str},
                "_page_title": title_str,
            }

        # Fallback: try the old portalplayer path
        pp_params: dict[str, str] = {
            "uid": self.uid,
            "userPassportStatus": self.passport,
            "autoplay": "true",
            "unsafeDisableUpsellHref": "true",
        }
        if self.callsign:
            pp_params["callsign"] = self.callsign
        if self.station_id:
            pp_params["station_id"] = self.station_id
        rp = self.session.get(
            self.config["endpoints"]["portalplayer"] + video_slug + "/",
            params=pp_params,
        )
        if rp.ok and "window.videoBridge = " in rp.text:
            return self._parse_video_bridge(rp.text)

        raise ValueError(
            f"No URS stream URL found for {video_slug!r} and portalplayer also failed.\n"
            "The video may require PBS Passport or is unavailable in your region."
        )

"""
            try:
                if _pbs_init.exists():
                    _pbs_txt = _pbs_init.read_text(encoding="utf-8")
                    if _PBS_SENTINEL not in _pbs_txt:
                        _start = _pbs_txt.find("    def _fetch_video_bridge(self, video_slug: str) -> dict:")
                        _end   = _pbs_txt.find("    def _parse_video_bridge(self, html: str) -> dict:")
                        if _start != -1 and _end != -1 and _end > _start:
                            _pbs_bak = _pbs_init.with_suffix(".py.bak")
                            if not _pbs_bak.exists():
                                _pbs_bak.write_text(_pbs_txt, encoding="utf-8")
                            _pbs_txt = _pbs_txt[:_start] + _PBS_NEW_BRIDGE + _pbs_txt[_end:]
                            _pbs_init.write_text(_pbs_txt, encoding="utf-8")
                            self._log("Patched PBS/__init__.py: rewrote _fetch_video_bridge for Next.js RSC.")
                        else:
                            self._log("PBS/__init__.py: method boundaries not found — skipped.")
                    else:
                        self._log("PBS/__init__.py already patched — skipped.")
            except Exception as _pbs_e:
                self._log(f"Note: could not patch PBS/__init__.py: {_pbs_e}")


            # ── Patch providers/__init__.py: add OMDb, reorder providers ─────────
            # Default order is IMDBApi → SIMKL → TMDB which means every download
            # hits SIMKL (a tracking site, not a metadata CDN) before TMDB.
            # Correct order: IMDBApi (skipped if unreachable) → TMDB → OMDb → SIMKL.
            _prov_init = d / "packages/envied/src/envied/core/providers/__init__.py"
            _PROV_SENTINEL = "OmdbProvider"
            _PROV_OLD_IMPORT = (
                "from envied.core.providers.imdbapi import IMDBApiProvider\n"
                "from envied.core.providers.simkl import SimklProvider\n"
                "from envied.core.providers.tmdb import TMDBProvider"
            )
            _PROV_NEW_IMPORT = (
                "from envied.core.providers.imdbapi import IMDBApiProvider\n"
                "from envied.core.providers.omdb import OmdbProvider\n"
                "from envied.core.providers.simkl import SimklProvider\n"
                "from envied.core.providers.tmdb import TMDBProvider"
            )
            _PROV_OLD_LIST = "ALL_PROVIDERS: list[type[MetadataProvider]] = [IMDBApiProvider, SimklProvider, TMDBProvider]"
            _PROV_NEW_LIST = "ALL_PROVIDERS: list[type[MetadataProvider]] = [IMDBApiProvider, TMDBProvider, OmdbProvider, SimklProvider]"
            try:
                if _prov_init.exists():
                    _prov_txt = _prov_init.read_text(encoding="utf-8")
                    if _PROV_SENTINEL not in _prov_txt:
                        _changed = False
                        if _PROV_OLD_IMPORT in _prov_txt:
                            _prov_txt = _prov_txt.replace(_PROV_OLD_IMPORT, _PROV_NEW_IMPORT)
                            _changed = True
                        if _PROV_OLD_LIST in _prov_txt:
                            _prov_txt = _prov_txt.replace(_PROV_OLD_LIST, _PROV_NEW_LIST)
                            _changed = True
                        if _changed:
                            _prov_bak = _prov_init.with_suffix(".py.bak")
                            if not _prov_bak.exists():
                                _prov_bak.write_text(_prov_init.read_text(encoding="utf-8"), encoding="utf-8")
                            _prov_init.write_text(_prov_txt, encoding="utf-8")
                            self._log("Patched providers/__init__.py: added OMDb, reordered providers.")
                        else:
                            self._log("providers/__init__.py: expected text not found — skipped.")
                    else:
                        self._log("providers/__init__.py already patched — skipped.")
            except Exception as _pe:
                self._log(f"Note: could not patch providers/__init__.py: {_pe}")

            # ── Patch core/config.py: add omdb_api_key field ─────────────────────
            # Required for the OMDb provider to read its key from envied.yaml.
            _config_py = d / "packages/envied/src/envied/core/config.py"
            _CONFIG_SENTINEL = "omdb_api_key"
            _CONFIG_OLD = '        self.tmdb_api_key: str = kwargs.get("tmdb_api_key") or ""\n        self.simkl_client_id'
            _CONFIG_NEW = (
                '        self.tmdb_api_key: str = kwargs.get("tmdb_api_key") or ""\n'
                '        self.omdb_api_key: str = kwargs.get("omdb_api_key") or ""\n'
                '        self.simkl_client_id'
            )
            try:
                if _config_py.exists():
                    _config_txt = _config_py.read_text(encoding="utf-8")
                    if _CONFIG_SENTINEL not in _config_txt:
                        if _CONFIG_OLD in _config_txt:
                            _config_bak = _config_py.with_suffix(".py.bak")
                            if not _config_bak.exists():
                                _config_bak.write_text(_config_txt, encoding="utf-8")
                            _config_py.write_text(_config_txt.replace(_CONFIG_OLD, _CONFIG_NEW), encoding="utf-8")
                            self._log("Patched core/config.py: added omdb_api_key field.")
                        else:
                            self._log("core/config.py: expected text not found — skipped.")
                    else:
                        self._log("core/config.py already patched — skipped.")
            except Exception as _ce:
                self._log(f"Note: could not patch core/config.py: {_ce}")

            self._step("yaml", "done")
            self._step("done", "done")
            self.progress.emit(1.0, "Done ✓")
            # Persist uv path so _qt_runsubprocess can find it on restart
            self.uv_exe_path = uv_exe
            self.finished.emit(True, "")

        except Exception as exc:
            import traceback
            self._log(f"INSTALL ERROR: {exc}")
            self._log(traceback.format_exc())
            self.finished.emit(False, str(exc))


# ── Main Window ───────────────────────────────────────────────────────────────

class EnvyLauncher(QMainWindow):

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.install_dir = Path(self.cfg["install_dir"])
        self._service_worker: QThread | None = None
        self._install_worker: InstallWorker | None = None

        # Register our log callback
        global _log_fn
        _log_fn = self._append_log

        # Download panel signals — defined here so QApplication exists first
        from PyQt6.QtCore import QObject as _QObject, pyqtSignal as _pyqtSignal
        class _DlSignals(_QObject):
            line      = _pyqtSignal(str)
            progress  = _pyqtSignal(int)
            episode   = _pyqtSignal(str)
            status    = _pyqtSignal(str)
            done      = _pyqtSignal(bool)
            prompt    = _pyqtSignal(str)
            raw_bytes = _pyqtSignal(bytes)   # raw PTY bytes → _TermView
        self._dl_signals = _DlSignals()
        self._dl_signals.line.connect(self._dl_append_line)
        self._dl_signals.progress.connect(self._dl_update_progress)
        self._dl_signals.episode.connect(self._dl_update_episode)
        self._dl_signals.status.connect(self._dl_update_status)
        self._dl_signals.done.connect(self._dl_finished)
        self._dl_signals.prompt.connect(self._dl_handle_prompt)
        self._dl_signals.raw_bytes.connect(lambda b: self._dl_term.write_bytes(b))

        # Set module-level reference for download panel signals
        global _main_window
        _main_window = self

        # (dialog bridge not needed — envied uses direct subprocess calls)

        self.setWindowTitle(APP_NAME)
        # Set window icon (title bar + taskbar)
        import sys as _sys
        from PyQt6.QtGui import QIcon
        from pathlib import Path as _Path
        if getattr(_sys, 'frozen', False):
            # PyInstaller bundles assets into sys._MEIPASS temp folder
            _base = _Path(getattr(_sys, '_MEIPASS', str(_Path(_sys.executable).parent)))
            _icon_path = _base / "assets" / "icon.ico"
            # Fallback: next to the exe (for portable/extracted builds)
            if not _icon_path.exists():
                _icon_path = _Path(_sys.executable).parent / "assets" / "icon.ico"
        else:
            # Running as script — look next to the script
            _icon_path = _Path(__file__).parent / "assets" / "icon.ico"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))
        self.resize(1100, 900)
        self._apply_palette()
        self._build_ui()

        if self._is_installed():
            self._populate_service_buttons()

    def _apply_palette(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {C['bg']};
                color: {C['text']};
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }}
            QPushButton {{
                background: {C['overlay']};
                color: {C['text']};
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C['green']}; color: {C['bg']}; }}
            QPushButton:disabled {{ background: {C['surface']}; color: {C['border']}; }}
            QLineEdit {{
                background: {C['surface']};
                color: {C['text']};
                border: 1px solid {C['border']};
                border-radius: 3px;
                padding: 5px;
            }}
            QLineEdit:focus {{ border-color: {C['green']}; }}
            QScrollBar:vertical {{
                background: {C['surface']}; width: 10px; border: none;
            }}
            QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 4px; }}
            QLabel {{ color: {C['text']}; }}
            QCheckBox {{ color: {C['text']}; spacing: 6px; }}
            QSlider::groove:horizontal {{
                border: 1px solid {C['border']}; height: 5px;
                background: {C['overlay']}; margin: 2px 0;
            }}
            QSlider::handle:horizontal {{
                background: {C['green']}; border: none;
                width: 16px; height: 16px; margin: -6px 0; border-radius: 3px;
            }}
            QProgressBar {{
                background: {C['surface']}; border: 1px solid {C['border']};
                border-radius: 4px; text-align: center; color: {C['text']};
            }}
            QProgressBar::chunk {{ background: {C['green']}; border-radius: 3px; }}
            QFrame[frameShape="4"], QFrame[frameShape="5"] {{
                color: {C['border']};
            }}
        """)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar ──
        sidebar = QFrame()
        sidebar.setFixedWidth(230)
        sidebar.setStyleSheet(f"background:{C['surface']};border-right:1px solid {C['border']};")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        # Logo
        logo = QLabel(APP_NAME)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"""
            font-size:16px;font-weight:bold;color:{C['green']};
            padding:20px 0 4px 0;
        """)
        sb_layout.addWidget(logo)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet(f"font-size:9px;color:{C['border']};padding-bottom:12px;")
        sb_layout.addWidget(ver)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(line)

        # Nav buttons
        self._nav_btns = {}
        for key, label in [
                ("download",        "Home"),
                ("downloads_folder","My Downloads"),
                ("extended",        "Extended Services"),
                ("install",         "Install / Update"),
                ("log",             "Log"),
                ("help",            "Help"),
                ("about",           "About"),
            ]:
            b = QPushButton(f"  {label}")
            b.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; color:{C['subtext']};
                    border:none; padding:10px 16px; text-align:left;
                    font-size:12px; border-radius:0;
                }}
                QPushButton:hover {{background:{C['overlay']};color:{C['text']};}}
            """)
            b.clicked.connect(lambda _, k=key: self._show_page(k))
            sb_layout.addWidget(b)
            self._nav_btns[key] = b

        sb_layout.addStretch()

        # ── Metadata provider status indicator ────────────────────────────────
        line_meta = QFrame(); line_meta.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(line_meta)

        meta_frame = QFrame()
        meta_frame.setStyleSheet("background:transparent;border:none;")
        meta_outer = QVBoxLayout(meta_frame)
        meta_outer.setContentsMargins(10, 6, 10, 6)
        meta_outer.setSpacing(3)

        meta_title = QLabel("Metadata")
        meta_title.setStyleSheet(f"color:{C['subtext']};font-size:10px;border:none;")
        meta_outer.addWidget(meta_title)

        from PyQt6.QtWidgets import QGridLayout
        meta_grid = QGridLayout()
        meta_grid.setContentsMargins(0, 0, 0, 0)
        meta_grid.setHorizontalSpacing(6)
        meta_grid.setVerticalSpacing(2)
        meta_grid.setColumnStretch(1, 1)
        meta_grid.setColumnStretch(3, 1)

        self._meta_status_labels: dict[str, QLabel] = {}
        for row_idx, (left, right) in enumerate((("IMDBApi", "TMDB"), ("OMDb", "SIMKL"))):
            for col_offset, provider_name in ((0, left), (2, right)):
                dot = QLabel("●")
                dot.setStyleSheet(f"color:{C['subtext']};font-size:9px;border:none;")
                lbl = QLabel(provider_name)
                lbl.setStyleSheet(f"color:{C['subtext']};font-size:10px;border:none;")
                meta_grid.addWidget(dot, row_idx, col_offset)
                meta_grid.addWidget(lbl, row_idx, col_offset + 1)
                self._meta_status_labels[provider_name] = dot

        meta_outer.addLayout(meta_grid)
        sb_layout.addWidget(meta_frame)

        # ── Batch Mode in sidebar — all on one row ────────────────────────────
        line_b = QFrame(); line_b.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(line_b)

        batch_sb = QFrame()
        batch_sb.setStyleSheet("background:transparent;border:none;")
        batch_sb_layout = QVBoxLayout(batch_sb)
        batch_sb_layout.setContentsMargins(10, 8, 9, 8)
        batch_sb_layout.setSpacing(4)

        # Single row: Batch Mode | slider | Run Batch
        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)
        self._batch_label = QLabel("Batch Mode")
        self._batch_label.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        batch_row.addWidget(self._batch_label)
        self._batch_slider = QSlider(Qt.Orientation.Horizontal)
        self._batch_slider.setRange(0, 1)
        self._batch_slider.setFixedWidth(44)
        self._batch_slider.valueChanged.connect(self._toggle_batch)
        batch_row.addWidget(self._batch_slider)
        self._run_batch_btn = QPushButton("Run Batch")
        self._run_batch_btn.setEnabled(False)
        self._run_batch_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};border:none;"
            f"padding:3px 8px;font-size:10px;border-radius:3px;")
        self._run_batch_btn.clicked.connect(self._run_batch)
        batch_row.addWidget(self._run_batch_btn)
        batch_sb_layout.addLayout(batch_row)

        # Batch file indicator below
        self._batch_file_lbl = QLabel("")
        self._batch_file_lbl.setStyleSheet(
            f"color:{C['green']};border:none;font-size:9px;")
        batch_sb_layout.addWidget(self._batch_file_lbl)

        # Clear button on its own line below the indicator
        self._clear_batch_btn = QPushButton("✕  Clear Batch")
        self._clear_batch_btn.setVisible(False)
        self._clear_batch_btn.setStyleSheet(
            f"background:transparent;color:{C['red']};"
            f"border:none;font-size:9px;padding:0 0;text-align:left;")
        self._clear_batch_btn.clicked.connect(self._clear_batch)
        batch_sb_layout.addWidget(self._clear_batch_btn)

        batch_container = QHBoxLayout()
        batch_container.setContentsMargins(0, 0, 0, 0)
        batch_container.setSpacing(0)
        batch_container.addWidget(batch_sb)
        vline = QFrame()
        vline.setFixedWidth(1)
        vline.setStyleSheet(f"background:{C['border']};border:none;")
        batch_container.addWidget(vline)
        sb_layout.addLayout(batch_container)

        line2 = QFrame(); line2.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(line2)
        self._status_badge = QLabel("● Not installed")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setStyleSheet(f"color:{C['red']};font-size:9px;padding:8px;")
        sb_layout.addWidget(self._status_badge)

        root.addWidget(sidebar)

        # ── Right: stacked pages ──
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._pages = {
            "download": self._build_download_page(),
            "extended": self._build_extended_page(),
            "install":  self._build_install_page(),
            "log":      self._build_log_page(),
            "help":     self._build_help_page(),
            "about":    self._build_about_page(),
        }
        for page in self._pages.values():
            self._stack.addWidget(page)

        self._show_page("download")
        self._refresh_status()

    def _show_page(self, key: str):
        if key == "downloads_folder":
            self._open_downloads_folder()
            return
        if key not in self._pages:
            return
        self._stack.setCurrentWidget(self._pages[key])
        for k, b in self._nav_btns.items():
            active = (k == key)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:{''+C['overlay'] if active else 'transparent'};
                    color:{C['text'] if active else C['subtext']};
                    border:none; padding:10px 16px; text-align:left;
                    font-size:12px; border-radius:0;
                    {'border-left:3px solid '+C['green']+';' if active else ''}
                }}
                QPushButton:hover {{background:{C['overlay']};color:{C['text']};}}
            """)
    # ── Download page ─────────────────────────────────────────────────────────

    def _build_download_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 10, 24, 20)
        #layout.setSpacing(4)

        # ── Header row: "Download" title + Envied Config right-aligned ──
        hdr_row = QHBoxLayout()
        hdr = QLabel("Download")
        hdr.setStyleSheet(f"font-size:20px;font-weight:bold;color:{C['green']};")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()

        btn_style = (f"QPushButton{{background:{C['surface']};color:{C['pink']};"
                     f"border:1px solid {C['border']};padding:4px 10px;"
                     f"border-radius:3px;font-size:11px;}}"
                     f"QPushButton:hover{{background:{C['green']};color:{C['bg']};}}")
        self._ec_btn = QPushButton("Envied Config")
        self._ec_btn.setStyleSheet(btn_style)
        self._ec_btn.clicked.connect(self._open_envied_config)
        hdr_row.addWidget(self._ec_btn)

        layout.addLayout(hdr_row)

        sub = QLabel("Search for a show, paste a URL, or browse by category.")
        sub.setStyleSheet(f"color:{C['subtext']};padding-bottom:0px;")
        layout.addWidget(sub)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ── Status banner + HLG toggle ──
        status_row = QHBoxLayout()
        self._dl_status = QLabel("EnvyUI not set up — go to Install tab")
        self._dl_status.setStyleSheet(
            f"color:{C['red']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")
        status_row.addWidget(self._dl_status, stretch=1)

        self._hlg_cb = QCheckBox("HLG")
        self._hlg_cb.setChecked(True)
        self._hlg_cb.setToolTip(
            "HLG (High Dynamic Range) — enabled by default.\n\n"
            "⚠  If your download fails with:\n"
            "    'Selection unavailable in UHD'\n"
            "    or a resolution/quality error,\n\n"
            "→  UNTICK this box before retrying.\n\n"
            "Not all content or services support HLG/HDR streams.\n"
            "Unticking forces SDR (standard definition range), which\n"
            "works on every service."
        )
        self._hlg_cb.setStyleSheet(
            "QCheckBox{color:#f9e2af;font-size:11px;font-weight:bold;padding:0 8px;}"
            "QCheckBox::indicator:unchecked{border:1px solid #a6adc8;}")
        status_row.addWidget(self._hlg_cb)
        layout.addLayout(status_row)

        # ── Search box ──
        search_lbl = QLabel("URL or Search")
        search_lbl.setStyleSheet(f"color:{C['subtext']};margin-top:2px;")
        layout.addWidget(search_lbl)
        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText(
            "Enter keyword(s) to search, or paste a direct video URL")
        layout.addWidget(self._search_entry)

        # ── Service buttons header (label + inline pagination) ──
        svc_header_row = QHBoxLayout()
        svc_header_row.setContentsMargins(0, 4, 0, 0)
        svc_lbl = QLabel("Services")
        svc_lbl.setStyleSheet(f"color:{C['subtext']};font-size:10px;")
        svc_header_row.addWidget(svc_lbl)
        svc_header_row.addStretch()
        self._svc_nav_widget = QWidget()
        self._svc_nav_widget.setStyleSheet("background:transparent;")
        self._svc_nav_layout = QHBoxLayout(self._svc_nav_widget)
        self._svc_nav_layout.setContentsMargins(0, 0, 0, 0)
        self._svc_nav_layout.setSpacing(4)
        svc_header_row.addWidget(self._svc_nav_widget)
        layout.addLayout(svc_header_row)

        self._svc_frame = QFrame()
        self._svc_frame.setStyleSheet(
            f"border:1px solid {C['border']};border-radius:4px;"
            f"background:{C['surface']};")
        self._svc_layout = QVBoxLayout(self._svc_frame)
        self._svc_layout.setContentsMargins(4, 4, 4, 4)

        self._svc_placeholder = QLabel(
            "Service buttons will appear here once EnvyUI is set up.\n"
            "Go to the Install tab to get started.")
        self._svc_placeholder.setStyleSheet(
            f"color:{C['border']};padding:16px;border:none;")
        self._svc_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._svc_layout.addWidget(self._svc_placeholder)

        # Scroll area for service buttons
        svc_scroll = QScrollArea()
        svc_scroll.setWidget(self._svc_frame)
        svc_scroll.setWidgetResizable(True)
        svc_scroll.setFixedHeight(125)
        svc_scroll.setStyleSheet("border:none;")
        layout.addWidget(svc_scroll)

        # ── Inline selection panel (hidden until needed) ────────────────────
        self._sel_panel = QFrame()
        self._sel_panel.setStyleSheet(
            f"background:{C['surface']};border:1px solid {C['green']};"
            f"border-radius:6px;")
        sel_layout = QVBoxLayout(self._sel_panel)
        sel_layout.setContentsMargins(12, 10, 12, 10)
        sel_layout.setSpacing(6)

        # Title
        self._sel_title = QLabel("Select")
        self._sel_title.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        sel_layout.addWidget(self._sel_title)

        # Series range input (shown only for series selection)
        self._sel_range_widget = QWidget()
        range_layout = QHBoxLayout(self._sel_range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_lbl = QLabel("Series (e.g. 1, 2..4, 0=all):")
        range_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        range_layout.addWidget(range_lbl)
        self._sel_range_input = QLineEdit()
        self._sel_range_input.setPlaceholderText("0 for all, or 1, 2..4")
        self._sel_range_input.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};padding:4px;")
        range_layout.addWidget(self._sel_range_input)
        self._sel_range_widget.setVisible(False)
        sel_layout.addWidget(self._sel_range_widget)

        # Scrollable list
        self._sel_scroll = QScrollArea()
        self._sel_scroll.setWidgetResizable(True)
        self._sel_scroll.setMinimumHeight(220)
        self._sel_scroll.setStyleSheet(
            f"background:{C['bg']};border:1px solid {C['border']};")
        self._sel_list_widget = QWidget()
        self._sel_list_layout = QVBoxLayout(self._sel_list_widget)
        self._sel_list_layout.setContentsMargins(6, 6, 6, 6)
        self._sel_list_layout.setSpacing(2)
        self._sel_scroll.setWidget(self._sel_list_widget)
        sel_layout.addWidget(self._sel_scroll)

        # Confirm/Cancel/Back buttons
        sel_btn_row = QHBoxLayout()
        self._sel_confirm_btn = QPushButton("✓  Confirm")
        self._sel_confirm_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 18px;border-radius:3px;")
        sel_btn_row.addWidget(self._sel_confirm_btn)
        self._sel_back_btn = QPushButton("←  Back")
        self._sel_back_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 18px;border-radius:3px;")
        self._sel_back_btn.setVisible(False)
        self._back_action = None  # set by each navigation stage
        self._sel_back_btn.clicked.connect(self._on_back_btn_clicked)
        sel_btn_row.addWidget(self._sel_back_btn)
        self._sel_cancel_btn = QPushButton("✕  Cancel")
        self._sel_cancel_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 18px;border-radius:3px;")
        sel_btn_row.addWidget(self._sel_cancel_btn)
        sel_btn_row.addStretch()
        # Select All/None — right side, shown only during multi-select
        sa_style = (f"background:{C['overlay']};color:{C['subtext']};"
                    f"border:none;padding:6px 14px;font-size:11px;border-radius:3px;")
        self._sel_all_btn = QPushButton("Select All")
        self._sel_all_btn.setStyleSheet(sa_style)
        self._sel_all_btn.setVisible(False)
        sel_btn_row.addWidget(self._sel_all_btn)
        self._sel_none_btn = QPushButton("Select None")
        self._sel_none_btn.setStyleSheet(sa_style)
        self._sel_none_btn.setVisible(False)
        sel_btn_row.addWidget(self._sel_none_btn)
        sel_layout.addLayout(sel_btn_row)

        self._sel_panel.setVisible(False)
        layout.addSpacing(10)
        layout.addWidget(self._sel_panel)

        # ── Download Options panel ────────────────────────────────────────────
        self._opts_panel = QFrame()
        self._opts_panel.setObjectName('optsPanel')
        self._opts_panel.setStyleSheet(
            f"QFrame#optsPanel{{background:{C['surface']};border:1px solid {C['green']};"
            f"border-radius:6px;}}")
        opts_layout = QVBoxLayout(self._opts_panel)
        opts_layout.setContentsMargins(12, 10, 12, 10)
        opts_layout.setSpacing(8)

        opts_title = QLabel("Download Options")
        opts_title.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        opts_layout.addWidget(opts_title)
        opts_hint = QLabel(
            "Best available, 2160p, 1080p and 720p work reliably on all modern streaming services. "
            "For older content or non-standard resolutions use Best available, or use "
            "Download by URL to see exactly what tracks are available first."
        )
        opts_hint.setWordWrap(True)
        opts_hint.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        opts_layout.addWidget(opts_hint)
        opts_hint = QLabel(
            "Defaults work for most downloads."
        )
        opts_hint.setWordWrap(True)
        opts_hint.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        opts_layout.addWidget(opts_hint)

        opts_grid = QHBoxLayout()
        opts_grid.setSpacing(16)

        # Quality
        q_col = QVBoxLayout()
        q_col.setSpacing(3)
        q_lbl = QLabel("Quality")
        q_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        q_col.addWidget(q_lbl)
        self._opts_quality = QComboBox()
        self._opts_quality.addItems(["Best available", "2160p", "1080p", "720p"])
        self._opts_quality.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"padding:3px 6px;border-radius:3px;")
        q_col.addWidget(self._opts_quality)
        opts_grid.addLayout(q_col)



        opts_grid.addStretch()
        opts_layout.addLayout(opts_grid)

        # Checkboxes row
        chk_row = QHBoxLayout()
        chk_row.setSpacing(20)
        chk_style = (
            "QCheckBox{color:#cdd6f4;font-size:12px;font-weight:bold;}"
            "QCheckBox::indicator:unchecked{border:1px solid #a6adc8;}"
        )
        self._opts_no_subs = QCheckBox("No subtitles")
        self._opts_no_subs.setStyleSheet(chk_style)
        self._opts_no_subs.setToolTip("Disable subtitle download entirely.")
        chk_row.addWidget(self._opts_no_subs)
        self._opts_slow = QCheckBox("Slow mode")
        self._opts_slow.setStyleSheet(chk_style)
        chk_row.addWidget(self._opts_slow)
        # Min/max delay fields — enabled only when slow mode is ticked
        _slow_lbl = QLabel("delay:")
        _slow_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        chk_row.addWidget(_slow_lbl)
        self._opts_slow_min = QLineEdit("10")
        self._opts_slow_min.setFixedWidth(36)
        self._opts_slow_min.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._opts_slow_min.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:3px;padding:2px 4px;font-size:11px;")
        chk_row.addWidget(self._opts_slow_min)
        _slow_to = QLabel("–")
        _slow_to.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        chk_row.addWidget(_slow_to)
        self._opts_slow_max = QLineEdit("60")
        self._opts_slow_max.setFixedWidth(36)
        self._opts_slow_max.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._opts_slow_max.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:3px;padding:2px 4px;font-size:11px;")
        chk_row.addWidget(self._opts_slow_max)
        _slow_sec = QLabel("secs")
        _slow_sec.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        chk_row.addWidget(_slow_sec)
        # Dim the fields when slow mode is off
        for _w in [_slow_lbl, self._opts_slow_min, _slow_to,
                   self._opts_slow_max, _slow_sec]:
            _w.setEnabled(False)
        self._opts_slow.toggled.connect(
            lambda on: [_w.setEnabled(on) for _w in [
                _slow_lbl, self._opts_slow_min, _slow_to,
                self._opts_slow_max, _slow_sec]])
        chk_row.addStretch()
        opts_layout.addLayout(chk_row)

        # Buttons row
        opts_btn_row = QHBoxLayout()
        self._opts_download_btn = QPushButton("✓  Download")
        self._opts_download_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 18px;border-radius:3px;")
        opts_btn_row.addWidget(self._opts_download_btn)
        self._opts_cancel_btn = QPushButton("✕  Cancel")
        self._opts_cancel_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 18px;border-radius:3px;")
        self._opts_cancel_btn.clicked.connect(self._opts_cancel)
        opts_btn_row.addWidget(self._opts_cancel_btn)
        opts_btn_row.addStretch()
        opts_layout.addLayout(opts_btn_row)

        self._opts_panel.setVisible(False)
        layout.addWidget(self._opts_panel)

        # ── URL Download panel ────────────────────────────────────────────────
        self._url_panel = QFrame()
        self._url_panel.setObjectName('urlPanel')
        self._url_panel.setStyleSheet(
            f"QFrame#urlPanel{{background:{C['surface']};border:1px solid {C['green']};"
            f"border-radius:6px;}}")
        url_layout = QVBoxLayout(self._url_panel)
        url_layout.setContentsMargins(12, 10, 12, 10)
        url_layout.setSpacing(8)

        url_title = QLabel("Download by URL")
        url_title.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        url_layout.addWidget(url_title)

        self._url_display = QLabel("")
        self._url_display.setWordWrap(True)
        self._url_display.setStyleSheet(
            f"color:{C['subtext']};font-size:11px;border:none;")
        url_layout.addWidget(self._url_display)

        # Track results area — hidden until Fetch Tracks runs
        self._url_tracks_widget = QWidget()
        url_tracks_layout = QVBoxLayout(self._url_tracks_widget)
        url_tracks_layout.setContentsMargins(0, 0, 0, 0)
        url_tracks_layout.setSpacing(4)

        url_q_row = QHBoxLayout()
        url_q_lbl = QLabel("Quality:")
        url_q_lbl.setFixedWidth(80)
        url_q_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        url_q_row.addWidget(url_q_lbl)
        self._url_quality = QComboBox()
        self._url_quality.addItems(["Best available"])
        self._url_quality.setStyleSheet(
            "QComboBox{background:#181825;color:#cdd6f4;"
            "border:1px solid #45475a;padding:3px 6px;border-radius:3px;}"
            "QComboBox::drop-down{width:18px;}"
            "QComboBox QAbstractItemView{background:#181825;color:#cdd6f4;"
            "selection-background-color:#a6e3a1;selection-color:#1e1e2e;}")
        url_q_row.addWidget(self._url_quality)
        url_q_row.addStretch()
        url_tracks_layout.addLayout(url_q_row)


        self._url_tracks_widget.setVisible(False)
        url_layout.addWidget(self._url_tracks_widget)

        self._url_fetch_status = QLabel(
            "Click ‘Fetch Tracks’ to see what’s available, "
            "or ‘Download’ to start immediately with best quality."
        )
        self._url_fetch_status.setWordWrap(True)
        self._url_fetch_status.setStyleSheet(
            f"color:{C['subtext']};font-size:11px;border:none;font-style:italic;")
        url_layout.addWidget(self._url_fetch_status)

        # Buttons
        url_btn_row = QHBoxLayout()
        self._url_download_btn = QPushButton("✓  Download")
        self._url_download_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 18px;border-radius:3px;")
        url_btn_row.addWidget(self._url_download_btn)
        self._url_cancel_btn = QPushButton("✕  Cancel")
        self._url_cancel_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 18px;border-radius:3px;")
        url_btn_row.addWidget(self._url_cancel_btn)
        url_btn_row.addStretch()
        self._url_fetch_btn = QPushButton("🔍  Fetch Tracks")
        self._url_fetch_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 14px;font-size:11px;border-radius:3px;")
        url_btn_row.addWidget(self._url_fetch_btn)
        url_layout.addLayout(url_btn_row)

        # No subtitles + Slow mode — always visible
        url_chk_style = (
            "QCheckBox{color:#cdd6f4;font-size:12px;font-weight:bold;}"
            "QCheckBox::indicator:unchecked{border:1px solid #a6adc8;}"
        )
        url_chk_row = QHBoxLayout()
        url_chk_row.setSpacing(20)
        self._url_no_subs = QCheckBox("No subtitles")
        self._url_no_subs.setStyleSheet(url_chk_style)
        self._url_no_subs.setToolTip("Skip subtitle download.")
        url_chk_row.addWidget(self._url_no_subs)
        self._url_slow = QCheckBox("Slow mode")
        self._url_slow.setStyleSheet(url_chk_style)
        url_chk_row.addWidget(self._url_slow)
        _url_slow_lbl = QLabel("delay:")
        _url_slow_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        url_chk_row.addWidget(_url_slow_lbl)
        self._url_slow_min = QLineEdit("10")
        self._url_slow_min.setFixedWidth(36)
        self._url_slow_min.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_slow_min.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:3px;padding:2px 4px;font-size:11px;")
        url_chk_row.addWidget(self._url_slow_min)
        _url_slow_to = QLabel("–")
        _url_slow_to.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        url_chk_row.addWidget(_url_slow_to)
        self._url_slow_max = QLineEdit("60")
        self._url_slow_max.setFixedWidth(36)
        self._url_slow_max.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_slow_max.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:3px;padding:2px 4px;font-size:11px;")
        url_chk_row.addWidget(self._url_slow_max)
        _url_slow_sec = QLabel("secs")
        _url_slow_sec.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        url_chk_row.addWidget(_url_slow_sec)
        for _w in [_url_slow_lbl, self._url_slow_min, _url_slow_to,
                   self._url_slow_max, _url_slow_sec]:
            _w.setEnabled(False)
        self._url_slow.toggled.connect(
            lambda on: [_w.setEnabled(on) for _w in [
                _url_slow_lbl, self._url_slow_min, _url_slow_to,
                self._url_slow_max, _url_slow_sec]])
        url_chk_row.addStretch()
        url_layout.addLayout(url_chk_row)

        self._url_panel.setVisible(False)
        layout.addWidget(self._url_panel)

        # ── Action chooser (inline) ───────────────────────────────────────────
        self._action_widget = QWidget()
        self._action_widget.setVisible(False)
        action_outer = QVBoxLayout(self._action_widget)
        action_outer.setContentsMargins(0, 8, 0, 0)
        action_outer.setSpacing(6)
        action_lbl = QLabel("Choose action")
        action_lbl.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        action_outer.addWidget(action_lbl)
        action_btn_style = (
            f"QPushButton{{background:{C['surface']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"padding:10px 16px;text-align:left;font-size:12px;}}"
            f"QPushButton:hover{{background:{C['overlay']};color:{C['text']};}}"
        )
        self._action_btns = {}
        for _lbl in ["Search by keyword(s)", "Greedy Search by URL",
                     "Download by URL", "Browse by Category"]:
            _btn = QPushButton(_lbl)
            _btn.setStyleSheet(action_btn_style)
            action_outer.addWidget(_btn)
            self._action_btns[_lbl] = _btn
        _action_close_btn = QPushButton("✕  Close")
        _action_close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C['subtext']};"
            f"border:none;padding:4px 0px;text-align:left;font-size:11px;}}"
            f"QPushButton:hover{{color:{C['text']};}}"
        )
        _action_close_btn.clicked.connect(
            lambda: self._action_widget.setVisible(False))
        action_outer.addWidget(_action_close_btn)
        layout.addWidget(self._action_widget)

        # Text input row — shown after Search/Greedy/Download action selected
        self._action_input_widget = QWidget()
        self._action_input_widget.setVisible(False)
        ai_layout = QHBoxLayout(self._action_input_widget)
        ai_layout.setContentsMargins(0, 4, 0, 0)
        ai_layout.setSpacing(8)
        self._action_input_lbl = QLabel("Enter text:")
        self._action_input_lbl.setStyleSheet(
            f"color:{C['subtext']};font-size:11px;border:none;")
        ai_layout.addWidget(self._action_input_lbl)
        self._action_input = QLineEdit()
        self._action_input.setStyleSheet(
            f"background:{C['surface']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:3px;padding:6px;")
        ai_layout.addWidget(self._action_input, stretch=1)
        self._action_go_btn = QPushButton("Go")
        self._action_go_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 16px;border-radius:3px;")
        ai_layout.addWidget(self._action_go_btn)
        self._action_cancel_btn = QPushButton("Cancel")
        self._action_cancel_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 12px;border-radius:3px;")
        ai_layout.addWidget(self._action_cancel_btn)
        layout.addWidget(self._action_input_widget)

        # ── Download output panel ─────────────────────────────────────────
        self._dl_panel = QWidget()
        self._dl_panel.setVisible(False)
        dl_panel_layout = QVBoxLayout(self._dl_panel)
        dl_panel_layout.setContentsMargins(0, 8, 0, 0)
        dl_panel_layout.setSpacing(6)
        self._dl_ep_label = QLabel("Preparing download...")
        self._dl_ep_label.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        dl_panel_layout.addWidget(self._dl_ep_label)
        self._dl_status_label = QLabel("")  # kept for signal compat, hidden permanently
        self._dl_status_label.setVisible(False)
        self._dl_progress = QProgressBar()  # kept for signal compat, hidden permanently
        self._dl_progress.setVisible(False)
        self._dl_term = _TermView(C['bg'], C['subtext'], scroll_thumb=C['border'])
        self._dl_term.setMinimumHeight(260)
        dl_panel_layout.addWidget(self._dl_term, stretch=1)
        self._dl_cancel_btn = QPushButton("\u2715  Cancel Download")
        self._dl_cancel_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 16px;border-radius:3px;")
        dl_panel_layout.addWidget(
            self._dl_cancel_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._dl_panel)
        self._dl_proc = None
        self._opts_extra_args = []
        self._url_panel_url = None  # current download subprocess

        layout.addStretch()

        # Poll batch file
        self._batch_timer = QTimer(self)
        self._batch_timer.timeout.connect(self._update_batch_indicator)
        self._batch_timer.start(2000)

        return page

    def _toggle_batch(self, value):
        enabled = (value == 1)
        self._batch_label.setStyleSheet(
            f"color:{C['green']};border:none;" if enabled
            else f"color:{C['text']};border:none;")
        self._run_batch_btn.setEnabled(enabled)

    def _update_batch_indicator(self):
        # batch.txt is written to cwd which is install_dir after bootstrap
        batch_path = self.install_dir / "batch.txt"
        # Also check cwd in case it differs
        cwd_path = Path(os.getcwd()) / "batch.txt"
        if batch_path.exists() or cwd_path.exists():
            found = batch_path if batch_path.exists() else cwd_path
            try:
                lines = found.read_text(encoding="utf-8").strip().splitlines()
                count = len([l for l in lines if l.strip()])
            except Exception:
                count = 0
            self._batch_file_lbl.setText(f"✅ batch.txt — {count} episode(s) queued")
            self._batch_file_lbl.setStyleSheet(f"color:{C['green']};border:none;")
            self._clear_batch_btn.setVisible(True)
        else:
            self._batch_file_lbl.setText("")
            self._clear_batch_btn.setVisible(False)

    def _clear_batch(self):
        """Delete batch.txt and reset the batch indicator."""
        for p in (self.install_dir / "batch.txt", Path(os.getcwd()) / "batch.txt"):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        self._batch_file_lbl.setText("")
        self._clear_batch_btn.setVisible(False)
        self._batch_slider.setValue(0)
        self._dl_status.setText("✅  Batch cleared — ready to start a new batch")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border-radius:4px;font-size:12px;")
        self._append_log("[batch] Batch list cleared.")

    def _run_batch(self):
        if _VF_LOADED:
            # Visual feedback — turn green and show "Starting..."
            self._run_batch_btn.setText("Starting...")
            self._run_batch_btn.setEnabled(False)
            self._run_batch_btn.setStyleSheet(
                f"background:{C['green']};color:{C['bg']};font-weight:bold;"
                f"border:none;padding:3px 8px;font-size:10px;border-radius:3px;")
            # Reset button after 3 seconds
            from PyQt6.QtCore import QTimer
            def _reset_btn():
                self._run_batch_btn.setText("Run Batch")
                self._run_batch_btn.setEnabled(False)
                self._run_batch_btn.setStyleSheet(
                    f"background:{C['overlay']};color:{C['text']};border:none;"
                    f"padding:3px 8px;font-size:10px;border-radius:3px;")
            QTimer.singleShot(3000, _reset_btn)
            self._do_run_batch()  # non-blocking: _launch_all_powershell starts its own thread

    def _do_run_batch(self):
        """Run batch.txt through the download panel."""
        batch_path = self.install_dir / "batch.txt"
        if not batch_path.exists():
            batch_path = Path(os.getcwd()) / "batch.txt"
        if not batch_path.exists():
            self._append_log("[batch] batch.txt not found")
            return
        try:
            lines = [l.strip() for l in batch_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception as e:
            self._append_log(f"[batch] Could not read batch.txt: {e}")
            return
        if not lines:
            self._append_log("[batch] batch.txt is empty")
            return
        self._append_log(f"[batch] Running {len(lines)} queued download(s)")
        # Each line is a JSON object {"cmd":[], "slow":bool, ...} or legacy array
        import json as _json
        episode_list = []
        for line in lines:
            try:
                parsed = _json.loads(line)
                if isinstance(parsed, list) and parsed:
                    # Legacy format — no slow mode
                    episode_list.append((parsed, str(self.install_dir), False, 10, 60))
                elif isinstance(parsed, dict) and parsed.get("cmd"):
                    episode_list.append((
                        parsed["cmd"],
                        str(self.install_dir),
                        bool(parsed.get("slow", False)),
                        int(parsed.get("slow_min", 10)),
                        int(parsed.get("slow_max", 60)),
                    ))
            except Exception:
                pass
        if episode_list:
            try:
                batch_path.unlink()
            except Exception:
                pass
            _launch_all_powershell(episode_list)

    def _svc_display(self, service_id: str) -> str:
        """Return the human-readable label for a service ID."""
        for svc in CORE_SERVICES:
            if svc["id"] == service_id:
                return svc["label"]
        return service_id

    def _populate_service_buttons(self, page: int = 0):
        """Populate service buttons for the given page (4 rows × 7 per page)."""
        COLS     = 7
        ROWS     = 4
        PER_PAGE = COLS * ROWS   # 28

        services   = list(CORE_SERVICES)
        total_pages = max(1, -(-len(services) // PER_PAGE))  # ceiling div
        page        = max(0, min(page, total_pages - 1))
        self._svc_page = page

        def _clear_item(item):
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count():
                    _clear_item(sub.takeAt(0))

        while self._svc_layout.count():
            _clear_item(self._svc_layout.takeAt(0))

        btn_style = (
            f"QPushButton{{background:{C['surface']};color:{C['green']};"
            f"border:1px solid {C['green']};border-radius:3px;"
            f"padding:4px 4px;font-size:11px;}}"
            f"QPushButton:hover{{background:{C['green']};color:{C['bg']};}}"
        )

        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        for col in range(COLS):
            grid.setColumnStretch(col, 1)

        page_svcs = services[page * PER_PAGE : (page + 1) * PER_PAGE]
        for i, svc in enumerate(page_svcs):
            row, col = divmod(i, COLS)
            btn = QPushButton(svc["label"])
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda _, s=svc["id"]: self._on_service_clicked(s))
            grid.addWidget(btn, row, col)
        self._svc_layout.addLayout(grid)

        # Clear then repopulate the inline nav widget in the Services header row
        while self._svc_nav_layout.count():
            item = self._svc_nav_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if total_pages > 1:
            nav_btn_style = (
                f"QPushButton{{background:{C['surface']};color:{C['green']};"
                f"border:1px solid {C['green']};border-radius:3px;"
                f"padding:2px 10px;font-size:10px;}}"
                f"QPushButton:hover{{background:{C['green']};color:{C['bg']};}}"
                f"QPushButton:disabled{{color:{C['border']};border-color:{C['border']};}}"
            )

            prev_btn = QPushButton("◀")
            prev_btn.setStyleSheet(nav_btn_style)
            prev_btn.setEnabled(page > 0)
            prev_btn.clicked.connect(lambda: self._populate_service_buttons(self._svc_page - 1))

            page_lbl = QLabel(f"{page + 1} / {total_pages}")
            page_lbl.setStyleSheet(f"color:{C['subtext']};font-size:10px;padding:0 4px;")

            next_btn = QPushButton("▶")
            next_btn.setStyleSheet(nav_btn_style)
            next_btn.setEnabled(page < total_pages - 1)
            next_btn.clicked.connect(lambda: self._populate_service_buttons(self._svc_page + 1))

            self._svc_nav_layout.addWidget(prev_btn)
            self._svc_nav_layout.addWidget(page_lbl)
            self._svc_nav_layout.addWidget(next_btn)

        self._dl_status.setText("✅  EnvyUI ready — choose a service to begin")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    def _opts_cancel(self):
        """Cancel from options panel — go back to service buttons."""
        self._opts_panel.setVisible(False)
        self._opts_extra_args = []
        self._action_widget.setVisible(False)
        self._action_input_widget.setVisible(False)
        self._dl_status.setText("✓ Ready — click a service button to start")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    def _opts_show(self, pending_confirm_fn, cancel_fn=None):
        """Show the Download Options panel. pending_confirm_fn resumes the download."""
        self._opts_extra_args = []
        self._sel_panel.setVisible(False)
        # Reset all options to defaults on each show
        self._opts_quality.setCurrentIndex(0)  # Best
        self._opts_no_subs.setChecked(False)
        self._opts_slow.setChecked(False)
        self._opts_slow_min.setText("10")
        self._opts_slow_max.setText("60")

        def _on_download():
            args = []
            q = self._opts_quality.currentText()
            if q != "Best available":
                args += ["-q", q.replace("p", "")]
            if self._opts_no_subs.isChecked():
                args += ["--no-subs"]
            self._opts_extra_args = args
            self._opts_panel.setVisible(False)
            pending_confirm_fn()

        def _on_cancel():
            self._opts_panel.setVisible(False)
            self._opts_extra_args = []
            # Reset status
            self._dl_status.setText("✓ Ready — click a service button to start")
            self._dl_status.setStyleSheet(
                f"color:{C['green']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            if cancel_fn:
                cancel_fn()

        try:
            self._opts_download_btn.clicked.disconnect()
            self._opts_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._opts_download_btn.clicked.connect(_on_download)
        self._opts_cancel_btn.clicked.connect(_on_cancel)
        self._opts_panel.setVisible(True)

    def _show_url_panel(self, url: str):
        """Show the URL Download panel."""
        self._url_panel_url = url
        self._url_display.setText(f"URL: {url[:90]}{'...' if len(url) > 90 else ''}")
        self._url_tracks_widget.setVisible(False)
        self._url_fetch_status.setText(
            "Click ‘Fetch Tracks’ to see what’s available, "
            "or ‘Download’ to start immediately with best quality."
        )
        self._url_fetch_status.setStyleSheet(
            f"color:{C['subtext']};font-size:11px;border:none;font-style:italic;")
        self._url_quality.clear()
        self._url_quality.addItems(["Best available"])
        self._url_no_subs.setChecked(False)
        self._url_slow.setChecked(False)
        self._url_slow_min.setText("10")
        self._url_slow_max.setText("60")
        try:
            self._url_download_btn.clicked.disconnect()
            self._url_cancel_btn.clicked.disconnect()
            self._url_fetch_btn.clicked.disconnect()
        except Exception:
            pass
        self._url_download_btn.clicked.connect(self._url_do_download)
        self._url_cancel_btn.clicked.connect(self._url_do_cancel)
        self._url_fetch_btn.clicked.connect(self._url_fetch_tracks)
        self._action_widget.setVisible(False)
        self._action_input_widget.setVisible(False)
        self._sel_panel.setVisible(False)
        self._opts_panel.setVisible(False)
        self._url_panel.setVisible(True)

    def _url_fetch_tracks(self):
        """Fetch available tracks using FetchTracksWorker."""
        url = getattr(self, '_url_panel_url', None)
        if not url:
            return
        svc = getattr(self, '_pending_service', None)
        if not svc:
            self._url_fetch_status.setText("Error: service not known — click a service button first")
            return
        # Find uv.exe
        import shutil as _sh
        from pathlib import Path as _P
        uv_exe = None
        for candidate in [_P.home() / ".local" / "bin" / "uv.exe"]:
            if candidate.exists():
                uv_exe = str(candidate)
                break
        if not uv_exe:
            uv_exe = _sh.which("uv") or "uv"

        self._url_fetch_status.setText("⏳ Fetching available tracks…")
        self._url_fetch_status.setStyleSheet(
            f"color:{C['yellow']};font-size:11px;border:none;font-style:italic;")
        self._url_fetch_btn.setEnabled(False)

        self._fetch_worker = FetchTracksWorker(uv_exe, self.install_dir, svc, url)
        self._fetch_worker.log_line.connect(self._append_log)
        self._fetch_worker.error.connect(self._url_fetch_error)
        self._fetch_worker.finished.connect(self._url_fetch_done)
        self._fetch_worker.start()

    def _url_fetch_error(self, err: str):
        self._url_fetch_status.setText(f"Error: {err}")
        self._url_fetch_status.setStyleSheet(
            f"color:{C['red']};font-size:11px;border:none;")
        self._url_fetch_btn.setEnabled(True)

    def _url_fetch_done(self, output: str):
        """Parse track output and populate dropdowns."""
        import re as _re

        # Parse unique heights from video lines: | 1920x1080 @ ...
        qualities = ["Best available"]
        seen_q = set()
        for m in _re.finditer(r'\|\s*\d+x(\d+)\s*@', output):
            h = int(m.group(1))
            label = f"{h}p"
            if label not in seen_q:
                qualities.append(label)
                seen_q.add(label)

        # Parse subtitle options
        subs = ["All available", "None"]
        seen_s = set()
        in_subs = False
        for line in output.splitlines():
            if _re.search(r'\d+\s+Subtitle', line):
                in_subs = True
            if in_subs and ('├' in line or '└' in line):
                m = _re.search(r'\[([^\]]+)\]\s*\|\s*([a-z]{2,3})(.*?)(?:\||$)', line)
                if m:
                    lang = m.group(2)
                    extra = m.group(3).strip()
                    label = f"{lang} SDH" if 'SDH' in extra else lang
                    if label not in seen_s:
                        subs.append(label)
                        seen_s.add(label)

        self._url_quality.clear()
        self._url_quality.addItems(qualities)
        self._url_tracks_widget.setVisible(True)

        if len(qualities) > 1:
            self._url_fetch_status.setText(
                f"✓ Found {len(qualities)-1} resolution(s). "
                "Select your preference then click Download."
            )
            self._url_fetch_status.setStyleSheet(
                f"color:{C['green']};font-size:11px;border:none;font-style:normal;")
        else:
            self._url_fetch_status.setText(
                "Could not parse tracks — see Log tab. "
                "You can still Download with best quality."
            )
            self._url_fetch_status.setStyleSheet(
                f"color:{C['yellow']};font-size:11px;border:none;font-style:italic;")
        self._url_fetch_btn.setEnabled(True)

    def _url_do_download(self):
        """Start download with selected options."""
        url = getattr(self, '_url_panel_url', None)
        if not url:
            return
        svc = getattr(self, '_pending_service', None)
        if not svc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, APP_NAME, "Service not known — please click a service button first.")
            return
        self._url_panel.setVisible(False)
        self._start_download(svc, [url])

    def _url_do_cancel(self):
        """Cancel URL panel — return to clean home state."""
        self._url_panel.setVisible(False)
        self._action_widget.setVisible(False)
        self._opts_extra_args = []
        self._dl_status.setText(
            "✓ Ready — click a service button to start")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    def closeEvent(self, event):
        """Clean up background processes before closing."""
        # Kill any running download process
        if hasattr(self, '_dl_proc') and self._dl_proc:
            try:
                self._dl_proc.terminate()
            except Exception:
                pass
        # Kill any running install worker
        if hasattr(self, '_install_worker') and self._install_worker:
            try:
                self._install_worker.terminate()
                self._install_worker.wait(2000)
            except Exception:
                pass
        # Kill any running service worker
        if hasattr(self, '_svc_worker') and self._svc_worker:
            try:
                self._svc_worker.terminate()
                self._svc_worker.wait(2000)
            except Exception:
                pass
        event.accept()

    def _open_downloads_folder(self):
        """Open the downloads folder in Windows Explorer."""
        downloads = None
        try:
            cfg = self.install_dir / "packages" / "envied" / "src" / "envied" / "envied.yaml"
            if cfg.exists():
                import yaml as _yaml
                data = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
                # envied.yaml stores download dir under directories.downloads
                dirs = (data or {}).get("directories", {}) or {}
                dl = dirs.get("downloads", "")
                if dl:
                    # Path may be relative to install_dir
                    dl_path = Path(dl) if Path(dl).is_absolute() else self.install_dir / dl
                    if dl_path.exists():
                        downloads = dl_path
        except Exception:
            pass
        if not downloads:
            # Fall back to the Downloads folder inside TwinVine
            fallback = self.install_dir / "Downloads"
            fallback.mkdir(exist_ok=True)
            downloads = fallback
        subprocess.Popen(["explorer", str(downloads)])


    def _yaml_path(self) -> Path:
        return self.install_dir / "packages" / "envied" / "src" / "envied" / "envied.yaml"

    def _load_dl_dir_from_yaml(self):
        """Populate the downloads folder field from envied.yaml."""
        try:
            cfg = self._yaml_path()
            if not cfg.exists():
                return
            import yaml as _yaml
            data = _yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            dl = (data.get("directories") or {}).get("downloads", "")
            # Only show if it's a custom (non-default) path
            if dl and dl.strip().lower() not in ("downloads", "downloads/", "downloads\\"):
                self._dl_dir_entry.setText(str(dl))
        except Exception:
            pass

    def _browse_dl_dir(self):
        start = self._dl_dir_entry.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select Downloads Folder", start)
        if chosen:
            self._dl_dir_entry.setText(chosen)

    def _save_dl_dir(self):
        """Write directories.downloads into envied.yaml using regex patch (preserves comments)."""
        import re as _re
        chosen = self._dl_dir_entry.text().strip()
        cfg = self._yaml_path()
        if not cfg.exists():
            self._dl_dir_status.setText("envied.yaml not found — install first.")
            self._dl_dir_status.setStyleSheet(f"color:{C['red']};font-size:11px;border:none;")
            return
        try:
            text = cfg.read_text(encoding="utf-8")
            posix = chosen.replace("\\", "/") if chosen else ""

            # Does a downloads: line already exist (possibly commented out)?
            uncomment_re = _re.compile(
                r'^([ \t]*)#[ \t]*(downloads:)[ \t]*.*$', _re.MULTILINE)
            existing_re = _re.compile(
                r'^([ \t]*downloads:)[ \t]*.*$', _re.MULTILINE)

            if existing_re.search(text):
                if posix:
                    # Update value in-place
                    new_text = existing_re.sub(
                        lambda m: f"{m.group(1)} {posix}", text, count=1)
                else:
                    # Clearing — comment the line out so YAML never sees a null
                    new_text = existing_re.sub(
                        lambda m: f"{m.group(1).replace(m.group(1).lstrip(), '')}# downloads: Downloads",
                        text, count=1)
            elif uncomment_re.search(text):
                if posix:
                    # Uncomment and set
                    new_text = uncomment_re.sub(
                        lambda m: f"{m.group(1)}{m.group(2)} {posix}", text, count=1)
                else:
                    # Already commented — leave as-is
                    new_text = text
            else:
                if posix:
                    # Append under directories: block
                    dirs_re = _re.compile(r'^(directories:\s*)$', _re.MULTILINE)
                    m = dirs_re.search(text)
                    if m:
                        new_text = text[:m.end()] + f"\n  downloads: {posix}" + text[m.end():]
                    else:
                        new_text = text + f"\ndirectories:\n  downloads: {posix}\n"
                else:
                    new_text = text

            cfg.write_text(new_text, encoding="utf-8")
            msg = f"Saved. Downloads folder: {chosen}" if chosen else "Cleared (using default)."
            self._dl_dir_status.setText(msg)
            self._dl_dir_status.setStyleSheet(f"color:{C['green']};font-size:11px;border:none;")
        except Exception as e:
            self._dl_dir_status.setText(f"Error: {e}")
            self._dl_dir_status.setStyleSheet(f"color:{C['red']};font-size:11px;border:none;")

    def _backup_settings(self):
        """Create a timestamped backup folder on the Desktop with envied.yaml, WVDs, PRDs, and Cookies."""
        import datetime as _dt
        cfg = self._yaml_path()
        if not cfg.exists():
            self._backup_status.setText("envied.yaml not found — install first.")
            self._backup_status.setStyleSheet(f"color:{C['red']};font-size:11px;border:none;")
            return
        try:
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            desktop = Path.home() / "Desktop"
            bak_dir = desktop / f"EnvyUI-Backup-{ts}"
            bak_dir.mkdir(parents=True, exist_ok=True)

            # envied.yaml
            shutil.copy2(cfg, bak_dir / "envied.yaml")

            # WVDs, PRDs, Cookies — copy entire folder if it exists and has files
            for folder_name in ("WVDs", "PRDs", "Cookies"):
                src = self.install_dir / folder_name
                if src.is_dir() and any(src.iterdir()):
                    shutil.copytree(src, bak_dir / folder_name, dirs_exist_ok=True)

            self._last_backup_path = bak_dir
            self._backup_status.setText(f"Saved to Desktop: {bak_dir.name}")
            self._backup_status.setStyleSheet(f"color:{C['green']};font-size:11px;border:none;")
            if hasattr(self, '_backup_open_btn'):
                self._backup_open_btn.setVisible(True)
        except Exception as e:
            self._backup_status.setText(f"Error: {e}")
            self._backup_status.setStyleSheet(f"color:{C['red']};font-size:11px;border:none;")

    def _open_backup_location(self):
        bak = getattr(self, '_last_backup_path', None)
        if bak and Path(bak).exists():
            subprocess.Popen(["explorer", str(bak)])
        elif bak:
            subprocess.Popen(["explorer", str(Path(bak).parent)])

    def _open_envied_config(self):
        cfg_path = self.install_dir / "packages/envied/src/envied/envied.yaml"
        if cfg_path.exists():
            subprocess.Popen(["notepad.exe", str(cfg_path)])
        else:
            QMessageBox.warning(self, APP_NAME, f"envied.yaml not found at:\n{cfg_path}")


    def _on_service_clicked(self, service_name: str):
        """Handle a service button click."""
        if hasattr(self, '_dl_panel') and self._dl_panel.isVisible():
            QMessageBox.information(self, APP_NAME,
                "A download is in progress. Please wait or cancel it first.")
            return

        self._pending_service = service_name
        text = self._search_entry.text().strip()

        if text:
            if "http" in text:
                self._search_entry.clear()
                self._show_url_panel(text)
                return
            else:
                self._search_entry.clear()
                self._run_search(service_name, text)
                return

        def _make_handler(lbl):
            def _h():
                self._action_widget.setVisible(False)
                for b in self._action_btns.values():
                    try: b.clicked.disconnect()
                    except Exception: pass
                self._on_action_chosen(lbl)
            return _h

        for lbl, btn in self._action_btns.items():
            try: btn.clicked.disconnect()
            except Exception: pass
            btn.clicked.connect(_make_handler(lbl))

        self._action_widget.setVisible(True)

    def _on_action_chosen(self, action: str):
        """Called when user clicks one of the inline action buttons."""
        service_name = self._pending_service

        if "Browse" not in action:
            if "Greedy" in action:
                hint = "Enter a URL for greedy search..."
            elif "Download" in action:
                hint = "Enter a URL for direct download..."
            else:
                hint = "Enter keyword(s) to search..."
            self._action_input_lbl.setText(hint)
            self._action_input.clear()
            self._action_input.setPlaceholderText(hint)
            self._action_input_widget.setVisible(True)
            self._action_input.setFocus()

            def _go():
                val = self._action_input.text().strip()
                if not val:
                    return
                self._action_input_widget.setVisible(False)
                try:
                    self._action_go_btn.clicked.disconnect()
                    self._action_cancel_btn.clicked.disconnect()
                    self._action_input.returnPressed.disconnect()
                except Exception:
                    pass
                if "Search" in action:
                    self._run_search(service_name, val)
                else:
                    self._show_url_panel(val)

            def _cancel_input():
                self._action_input_widget.setVisible(False)
                try:
                    self._action_go_btn.clicked.disconnect()
                    self._action_cancel_btn.clicked.disconnect()
                    self._action_input.returnPressed.disconnect()
                except Exception:
                    pass

            try:
                self._action_go_btn.clicked.disconnect()
                self._action_cancel_btn.clicked.disconnect()
                self._action_input.returnPressed.disconnect()
            except Exception:
                pass
            self._action_go_btn.clicked.connect(_go)
            self._action_cancel_btn.clicked.connect(_cancel_input)
            self._action_input.returnPressed.connect(_go)
            return

        # Browse by Category — fetch categories then shows
        self._show_category_browser(service_name)

    def _show_category_browser(self, service_name: str):
        """Fetch categories for a service and show them in the selection panel."""
        self._dl_status.setText(f"⏳ Loading categories for {self._svc_display(service_name)}…")
        self._dl_status.setStyleSheet(
            f"color:{C['yellow']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        worker = CategoryWorker(service_name)
        worker.done.connect(
            lambda cats: self._show_category_list(service_name, cats))
        worker.error.connect(lambda msg: (
            self._dl_status.setText(f"Category error: {msg.splitlines()[0]}"),
            self._dl_status.setStyleSheet(
                f"color:{C['red']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;"),
            self._append_log(f"[category] {msg}"),
        ))
        worker.start()
        self._category_worker = worker

    def _show_category_list(self, service_name: str, categories: list):
        """Show category radio-button list in _sel_panel."""
        self._last_categories = (service_name, categories)
        self._dl_status.setText(
            f"✅ {len(categories)} categories — select one to browse")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        panel = self._sel_panel
        self._sel_title.setText(f"Browse categories — {self._svc_display(service_name)}")
        self._sel_range_widget.setVisible(False)

        while self._sel_list_layout.count():
            child = self._sel_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        group = QButtonGroup(panel)
        for i, cat in enumerate(categories):
            rb = QRadioButton(cat["name"])
            rb.setProperty("cat_data", cat)
            rb.setStyleSheet(
                "QRadioButton {"
                f"color:{C['text']};font-size:12px;padding:5px 8px;"
                f"border:1px solid {C['border']};border-radius:3px;"
                f"background:{C['bg']};}}"
                "QRadioButton:hover {"
                f"background:{C['surface']};}}"
                "QRadioButton::indicator {"
                "width:14px;height:14px;border-radius:7px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                "QRadioButton::indicator:checked {"
                f"background:{C['green']};border:2px solid {C['green']};}}"
            )
            if i == 0:
                rb.setChecked(True)
            group.addButton(rb, i)
            self._sel_list_layout.addWidget(rb)
        self._sel_list_layout.addStretch()

        self._sel_all_btn.setVisible(False)
        self._sel_none_btn.setVisible(False)

        def _confirm():
            checked_id = group.checkedId()
            if checked_id < 0:
                return
            cat = categories[checked_id]
            panel.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
            except Exception:
                pass
            if not cat.get("id"):  # separator entries (e.g. UKTV "── Channels ──")
                return
            self._fetch_category_shows(service_name, cat["id"], cat["name"])

        def _cancel():
            panel.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
            except Exception:
                pass

        try:
            self._sel_confirm_btn.clicked.disconnect()
            self._sel_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_confirm_btn.clicked.connect(_confirm)
        self._sel_cancel_btn.clicked.connect(_cancel)
        self._back_action = None
        self._sel_back_btn.setVisible(False)
        panel.setVisible(True)
        panel.raise_()

    def _fetch_category_shows(self, service_name: str,
                               category_id: str, category_name: str):
        """Fetch shows in the selected category and show as search results."""
        # Two-level navigation: RKTN, CRAV, CBC → genre list
        if category_id.startswith("cbc-type:"):
            section = category_id.split(":", 1)[1]  # "shows" or "films"
            svc_sub = f"CBC:{section}"
            self._dl_status.setText(f"⏳ Loading {category_name} genres…")
            self._dl_status.setStyleSheet(
                f"color:{C['yellow']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            worker = CategoryWorker(svc_sub)
            worker.done.connect(lambda cats: self._show_category_list(service_name, cats))
            worker.error.connect(lambda msg: (
                self._append_log(f"[category] {msg}"),
                self._dl_status.setText(f"Could not load genres: {msg.splitlines()[0]}"),
                self._dl_status.setStyleSheet(
                    f"color:{C['red']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;"),
            ))
            worker.start()
            self._category_worker = worker
            return

        if category_id.startswith("crav-type:"):
            section = category_id.split(":", 1)[1]  # "movies" or "shows"
            svc_sub = f"CRAV:{section}"
            self._dl_status.setText(f"⏳ Loading {category_name} genres…")
            self._dl_status.setStyleSheet(
                f"color:{C['yellow']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            worker = CategoryWorker(svc_sub)
            worker.done.connect(lambda cats: self._show_category_list(service_name, cats))
            worker.error.connect(lambda msg: (
                self._append_log(f"[category] {msg}"),
                self._dl_status.setText(f"Could not load genres: {msg.splitlines()[0]}"),
                self._dl_status.setStyleSheet(
                    f"color:{C['red']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;"),
            ))
            worker.start()
            self._category_worker = worker
            return

        if category_id.startswith("rktn-type:"):
            content_type = category_id.split(":", 1)[1]  # "movies" or "tv_shows"
            svc_sub = f"RKTN:{content_type}"
            self._dl_status.setText(
                f"⏳ Loading {category_name} genres…")
            self._dl_status.setStyleSheet(
                f"color:{C['yellow']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            worker = CategoryWorker(svc_sub)
            worker.done.connect(lambda cats: self._show_category_list(service_name, cats))
            worker.error.connect(lambda msg: (
                self._append_log(f"[category] {msg}"),
                self._dl_status.setText(f"Could not load genres: {msg.splitlines()[0]}"),
                self._dl_status.setStyleSheet(
                    f"color:{C['red']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;"),
            ))
            worker.start()
            self._category_worker = worker
            return

        if category_id.startswith("seven-type:"):
            section = category_id.split(":", 1)[1]  # "shows", "movies", "news", "sport"
            svc_sub = f"SEVEN:{section}"
            self._dl_status.setText(f"⏳ Loading {category_name} shelves…")
            self._dl_status.setStyleSheet(
                f"color:{C['yellow']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            worker = CategoryWorker(svc_sub)
            worker.done.connect(lambda cats: self._show_category_list(service_name, cats))
            worker.error.connect(lambda msg: (
                self._append_log(f"[category] {msg}"),
                self._dl_status.setText(f"Could not load genres: {msg.splitlines()[0]}"),
                self._dl_status.setStyleSheet(
                    f"color:{C['red']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;"),
            ))
            worker.start()
            self._category_worker = worker
            return

        if category_id in ("SBS:tv", "SBS:movies"):
            self._dl_status.setText(f"⏳ Loading {category_name} genres…")
            self._dl_status.setStyleSheet(
                f"color:{C['yellow']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;")
            worker = CategoryWorker(category_id)
            worker.done.connect(lambda cats: self._show_category_list(service_name, cats))
            worker.error.connect(lambda msg: (
                self._append_log(f"[category] {msg}"),
                self._dl_status.setText(f"Could not load genres: {msg.splitlines()[0]}"),
                self._dl_status.setStyleSheet(
                    f"color:{C['red']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;"),
            ))
            worker.start()
            self._category_worker = worker
            return

        self._dl_status.setText(
            f"⏳ Loading '{category_name}' shows from {self._svc_display(service_name)}…")
        self._dl_status.setStyleSheet(
            f"color:{C['yellow']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        worker = CategoryShowsWorker(service_name, category_id, category_name)
        worker.done.connect(
            lambda shows: self._show_search_results(
                service_name, shows,
                total_count=worker.total_count,
                category_name=category_name,
            ))
        worker.error.connect(lambda msg: (
            self._append_log(f"[category shows] {msg}"),
            self._dl_status.setText(f"Could not load category: {msg.splitlines()[0]}"),
            self._dl_status.setStyleSheet(
                f"color:{C['red']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;"),
        ))
        worker.start()
        self._category_shows_worker = worker

    def _svc_display(self, service_id: str) -> str:
        """Return the human-readable label for a service id (e.g. 'iP' → 'BBC iPlayer')."""
        for svc in CORE_SERVICES:
            if svc["id"] == service_id:
                return svc["label"]
        return service_id

    def _run_search(self, service_name: str, term: str):
        """Run SearchWorker and show results in the selection panel."""
        self._dl_status.setText(f"⏳ Searching {self._svc_display(service_name)} for '{term}'…")
        self._dl_status.setStyleSheet(
            f"color:{C['yellow']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        worker = SearchWorker(service_name, term)
        worker.results_ready.connect(lambda results: self._show_search_results(service_name, results))
        worker.error.connect(self._on_search_error)
        worker.start()
        self._search_worker = worker

    def _on_search_error(self, msg: str):
        self._dl_status.setText(f"Search error: {msg}")
        self._dl_status.setStyleSheet(
            f"color:{C['red']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")
        self._append_log(f"[search error] {msg}")

    def _show_search_results(self, service_name: str, results: list,
                              total_count: int = 0, category_name: str = ""):
        """Populate _sel_panel with search results as radio buttons."""
        self._last_search_results = (service_name, results, total_count, category_name)
        svc_label = self._svc_display(service_name)
        if total_count and total_count > len(results):
            count_str = f"top {len(results):,} of {total_count:,}"
            note      = f" — showing top {len(results):,} of {total_count:,} titles"
        else:
            count_str = str(len(results))
            note      = ""
        self._dl_status.setText(f"✅ {count_str} result(s) for {svc_label} — select a show")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        panel     = self._sel_panel
        sel_title = (f"{category_name}{note}" if category_name else f"Select a show — {svc_label}")
        self._sel_title.setText(sel_title)
        self._sel_range_widget.setVisible(False)

        while self._sel_list_layout.count():
            child = self._sel_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        group = QButtonGroup(panel)
        for i, item in enumerate(results):
            label = item.get("title", "Unknown")
            syn   = item.get("synopsis", "")
            display = f"{label}\n  {syn[:80]}" if syn else label
            rb = QRadioButton(display)
            rb.setProperty("result_data", item)
            rb.setStyleSheet(
                "QRadioButton {"
                f"color:{C['text']};font-size:12px;padding:5px 8px;"
                f"border:1px solid {C['border']};border-radius:3px;"
                f"background:{C['bg']};}}"
                "QRadioButton:hover {"
                f"background:{C['surface']};}}"
                "QRadioButton::indicator {"
                "width:14px;height:14px;border-radius:7px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                "QRadioButton::indicator:checked {"
                f"background:{C['green']};border:2px solid {C['green']};}}"
            )
            if i == 0:
                rb.setChecked(True)
            group.addButton(rb, i)
            self._sel_list_layout.addWidget(rb)
        self._sel_list_layout.addStretch()

        self._sel_all_btn.setVisible(False)
        self._sel_none_btn.setVisible(False)

        def _confirm():
            checked_id = group.checkedId()
            if checked_id < 0:
                return
            selected = results[checked_id]
            panel.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
            except Exception:
                pass
            self._on_show_selected(service_name, selected)

        def _cancel():
            panel.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
            except Exception:
                pass
            self._reset_status()

        try:
            self._sel_confirm_btn.clicked.disconnect()
            self._sel_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_confirm_btn.clicked.connect(_confirm)
        self._sel_cancel_btn.clicked.connect(_cancel)
        lc = getattr(self, "_last_categories", None)
        if category_name and lc:
            self._back_action = lambda: self._show_category_list(lc[0], lc[1])
            self._sel_back_btn.setVisible(True)
        else:
            self._back_action = None
            self._sel_back_btn.setVisible(False)
        panel.setVisible(True)
        panel.raise_()

    def _on_show_selected(self, service_name: str, show: dict):
        """After user picks a show, fetch its episodes if supported."""
        url = show.get("url", "")
        if not url:
            QMessageBox.warning(self, APP_NAME, "Could not determine show URL.")
            return

        # Movies on Pluto/Tubi go straight to download — no episode list
        if service_name in ("PLUTO", "TUBI") and "/movies/" in url:
            self._pending_service = service_name
            self._start_download(service_name, [url])
            return

        # RKTN movies go through the standard download options panel (same as episodes)
        if service_name == "RKTN" and "/movies/" in url:
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # ROKU movies go to opts panel — type is encoded as /movie/ in the URL by _roku_search()
        if service_name == "ROKU" and "/movie/" in url:
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # VM: direct vod/replay URLs (catchup episodes) go straight to options — no series to browse
        if service_name == "VM" and ("/watch/vod/" in url or "/replay/" in url):
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # CWTV: movie URLs and direct episode URLs (?play=) go straight to options
        if service_name == "CWTV" and ("/movies/" in url or "?play=" in url):
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # PBS: direct video URLs go straight to options; show URLs → episode browse
        if service_name == "PBS" and "/video/" in url:
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # CRAV: movie URLs go straight to options; series URLs → episode browse
        if service_name == "CRAV" and "/movie/" in url:
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        # CBS: direct episode URLs go straight to options
        if service_name == "CBS" and "/shows/video/" in url:
            self._pending_service = service_name
            self._opts_show(lambda: self._start_download(service_name, [url]))
            return

        if service_name not in BROWSE_SUPPORTED:
            # Show URL panel so user can download directly
            self._show_url_panel(url)
            return

        self._dl_status.setText(f"⏳ Fetching episodes for '{show.get('title', '')}' from {self._svc_display(service_name)}…")
        self._dl_status.setStyleSheet(
            f"color:{C['yellow']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        worker = BrowseWorker(service_name, url)
        worker.status.connect(self._append_log)
        worker.done.connect(lambda episodes: self._show_series_selection(service_name, episodes))
        worker.error.connect(lambda msg: (
            self._append_log(f"[browse error] {msg}"),
            self._dl_status.setText(f"Could not fetch episodes — {msg.splitlines()[0]}"),
            self._dl_status.setStyleSheet(
                f"color:{C['red']};background:{C['surface']};padding:8px;"
                f"border:1px solid {C['border']};border-radius:3px;"),
        ))
        worker.start()
        self._browse_worker = worker

    def _show_series_selection(self, service_name: str, all_episodes: list):
        """If multiple series, let user pick which ones; then show episodes."""
        self._last_all_episodes = (service_name, all_episodes)
        series_groups = {}
        for ep in all_episodes:
            s_no = ep.get("series_no", "0")
            series_groups.setdefault(s_no, []).append(ep)

        if len(series_groups) <= 1:
            self._series_selector_was_shown = False
            self._show_episode_selection(service_name, all_episodes)
            return

        self._series_selector_was_shown = True

        series_list = sorted(series_groups.keys(),
                             key=lambda x: int(x) if x.isdigit() else 0)

        self._dl_status.setText(
            f"✅ {len(series_list)} seasons found — select seasons to download")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        panel = self._sel_panel
        self._sel_title.setText(f"Select series — {self._svc_display(service_name)}")
        self._sel_range_widget.setVisible(False)

        while self._sel_list_layout.count():
            child = self._sel_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        checkboxes = []
        for s_no in series_list:
            count = len(series_groups[s_no])
            label = (f"Season {s_no}" if s_no not in ("0", "") else "Specials")
            display = f"{label}  ({count} episode{'s' if count != 1 else ''})"
            cb = QCheckBox(display)
            cb.setProperty("series_no", s_no)
            cb.setStyleSheet(
                "QCheckBox {"
                f"color:{C['text']};font-size:12px;padding:5px 8px;"
                f"border:1px solid {C['border']};border-radius:3px;"
                f"background:{C['bg']};}}"
                "QCheckBox:hover {"
                f"background:{C['surface']};}}"
                "QCheckBox::indicator {"
                "width:14px;height:14px;border-radius:2px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                "QCheckBox::indicator:checked {"
                f"background:{C['green']};border:2px solid {C['green']};}}"
            )
            checkboxes.append(cb)
            self._sel_list_layout.addWidget(cb)
        self._sel_list_layout.addStretch()

        self._sel_all_btn.setVisible(True)
        self._sel_none_btn.setVisible(True)
        try:
            self._sel_all_btn.clicked.disconnect()
            self._sel_none_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_all_btn.clicked.connect(
            lambda: [cb.setChecked(True) for cb in checkboxes])
        self._sel_none_btn.clicked.connect(
            lambda: [cb.setChecked(False) for cb in checkboxes])

        def _confirm():
            selected = {cb.property("series_no") for cb in checkboxes if cb.isChecked()}
            if not selected:
                QMessageBox.information(
                    self, APP_NAME, "Please select at least one series.")
                return
            panel.setVisible(False)
            self._sel_all_btn.setVisible(False)
            self._sel_none_btn.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
                self._sel_all_btn.clicked.disconnect()
                self._sel_none_btn.clicked.disconnect()
            except Exception:
                pass
            filtered = [ep for ep in all_episodes
                        if ep.get("series_no") in selected]
            self._show_episode_selection(service_name, filtered)

        def _cancel():
            panel.setVisible(False)
            self._sel_all_btn.setVisible(False)
            self._sel_none_btn.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
                self._sel_all_btn.clicked.disconnect()
                self._sel_none_btn.clicked.disconnect()
            except Exception:
                pass
            self._reset_status()

        try:
            self._sel_confirm_btn.clicked.disconnect()
            self._sel_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_confirm_btn.clicked.connect(_confirm)
        self._sel_cancel_btn.clicked.connect(_cancel)
        last_sr = getattr(self, "_last_search_results", None)
        if last_sr:
            self._back_action = lambda: self._show_search_results(*last_sr)
        else:
            self._back_action = None
        self._sel_back_btn.setVisible(self._back_action is not None)
        panel.setVisible(True)
        panel.raise_()

    def _on_back_btn_clicked(self):
        if callable(self._back_action):
            self._back_action()

    def _reset_status(self):
        """Reset the status bar back to the idle/ready state."""
        self._dl_status.setText("✅  EnvyUI ready — choose a service to begin")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    def _show_episode_selection(self, service_name: str, episodes: list):
        """Populate _sel_panel with episode checkboxes grouped by series."""
        # Single item — skip the selection panel and go straight to download options
        if len(episodes) == 1:
            url = episodes[0].get("url", "")
            if url:
                ep_title = episodes[0].get("title", "")
                label = ep_title if ep_title else "1 episode found"
                self._dl_status.setText(f"✅ {label} — ready to download")
                self._dl_status.setStyleSheet(
                    f"color:{C['green']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;")
                if hasattr(self, '_opts_show'):
                    self._opts_show(
                        lambda: self._start_download(service_name, [url]),
                        self._reset_status,
                    )
                else:
                    self._start_download(service_name, [url])
                return

        self._dl_status.setText(f"✅ {len(episodes)} episode(s) — select episodes to download")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

        panel = self._sel_panel
        self._sel_title.setText(f"Select episodes — {self._svc_display(service_name)}")
        self._sel_range_widget.setVisible(False)

        while self._sel_list_layout.count():
            child = self._sel_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        checkboxes = []
        current_series = None
        for ep in episodes:
            s_no = ep.get("series_no", "0")
            if s_no != current_series:
                current_series = s_no
                series_lbl = QLabel(f"Season {s_no}" if s_no not in ("0", "") else "Specials")
                series_lbl.setStyleSheet(
                    f"color:{C['subtext']};font-size:10px;font-weight:bold;"
                    f"padding:4px 0 2px 8px;border:none;background:transparent;")
                self._sel_list_layout.addWidget(series_lbl)

            ep_title   = ep.get("title", "Unknown")
            ep_synopsis = ep.get("synopsis", "")
            s_num = ep.get("series_no", "")
            e_num = ep.get("ep_no", "")
            if s_num and s_num not in ("0", "") and e_num:
                prefix = f"S{s_num}·E{e_num} "
            elif e_num:
                prefix = f"E{e_num} "
            else:
                prefix = ""
            headline = f"{prefix}{ep_title}"
            display = f"{headline}\n    {ep_synopsis[:80]}" if ep_synopsis else headline
            cb = QCheckBox(display)
            cb.setProperty("episode_url", ep.get("url", ""))
            cb.setStyleSheet(
                "QCheckBox {"
                f"color:{C['text']};font-size:12px;padding:5px 8px;"
                f"border:1px solid {C['border']};border-radius:3px;"
                f"background:{C['bg']};}}"
                "QCheckBox:hover {"
                f"background:{C['surface']};}}"
                "QCheckBox::indicator {"
                "width:14px;height:14px;border-radius:2px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                "QCheckBox::indicator:checked {"
                f"background:{C['green']};border:2px solid {C['green']};}}"
            )
            checkboxes.append(cb)
            self._sel_list_layout.addWidget(cb)
        self._sel_list_layout.addStretch()

        self._sel_all_btn.setVisible(True)
        self._sel_none_btn.setVisible(True)
        try:
            self._sel_all_btn.clicked.disconnect()
            self._sel_none_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
        self._sel_none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])

        def _confirm():
            selected_urls = [cb.property("episode_url") for cb in checkboxes if cb.isChecked()]
            if not selected_urls:
                QMessageBox.information(self, APP_NAME, "Please select at least one episode.")
                return
            panel.setVisible(False)
            self._sel_all_btn.setVisible(False)
            self._sel_none_btn.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
                self._sel_all_btn.clicked.disconnect()
                self._sel_none_btn.clicked.disconnect()
            except Exception:
                pass
            if hasattr(self, '_opts_show'):
                self._opts_show(
                    lambda: self._start_download(service_name, selected_urls),
                    lambda: None
                )
            else:
                self._start_download(service_name, selected_urls)

        def _cancel():
            panel.setVisible(False)
            self._sel_all_btn.setVisible(False)
            self._sel_none_btn.setVisible(False)
            try:
                self._sel_confirm_btn.clicked.disconnect()
                self._sel_cancel_btn.clicked.disconnect()
                self._sel_all_btn.clicked.disconnect()
                self._sel_none_btn.clicked.disconnect()
            except Exception:
                pass
            self._reset_status()

        try:
            self._sel_confirm_btn.clicked.disconnect()
            self._sel_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._sel_confirm_btn.clicked.connect(_confirm)
        self._sel_cancel_btn.clicked.connect(_cancel)

        # Back: go to season selector if it was shown, else go to show list
        last_ae = getattr(self, "_last_all_episodes", None)
        if getattr(self, "_series_selector_was_shown", False) and last_ae and last_ae[0] == service_name:
            _ae_snapshot = last_ae[1]
            self._back_action = lambda: self._show_series_selection(service_name, _ae_snapshot)
        else:
            last_sr = getattr(self, "_last_search_results", None)
            self._back_action = (lambda lsr=last_sr: self._show_search_results(*lsr)) if last_sr else None
        self._sel_back_btn.setVisible(True)
        panel.setVisible(True)
        panel.raise_()

    def _start_download(self, service_name: str, urls: list):
        """Build episode command list and start download via _launch_all_powershell."""
        uv_exe = self._resolve_uv()

        # Collect extra args — opts panel sets _opts_extra_args before calling us;
        # URL-panel direct downloads bypass opts panel so read URL panel opts here.
        extra = list(getattr(self, '_opts_extra_args', []))
        self._opts_extra_args = []  # consume immediately so it never carries to next download
        if not extra:
            url_q = self._url_quality.currentText() if hasattr(self, '_url_quality') else "Best available"
            if url_q and url_q != "Best available":
                extra += ["-q", url_q.replace("p", "")]
            if hasattr(self, '_url_no_subs') and self._url_no_subs.isChecked():
                extra += ["--no-subs"]

        # HLG/UHD range flag is BBC iPlayer only — other services don't support it
        if (service_name.upper() in ("BBC", "IP")
                and hasattr(self, '_hlg_cb') and self._hlg_cb.isChecked()
                and "--range" not in extra):
            extra += ["--range", "HLG"]

        # Batch mode — append to batch.txt instead of downloading immediately
        if hasattr(self, '_batch_slider') and self._batch_slider.value() == 1:
            batch_path = self.install_dir / "batch.txt"
            import json as _json
            # Read slow mode settings at the moment this episode is added
            _ep_slow = hasattr(self, '_opts_slow') and self._opts_slow.isChecked()
            _ep_slow_min, _ep_slow_max = 10, 60
            if _ep_slow:
                try: _ep_slow_min = max(1, int(self._opts_slow_min.text()))
                except ValueError: pass
                try: _ep_slow_max = max(_ep_slow_min, int(self._opts_slow_max.text()))
                except ValueError: pass
            lines = []
            for url in urls:
                cmd = [uv_exe, "run", "--no-sync", "envied", "dl"] + extra + [service_name, url]
                entry = {"cmd": cmd, "slow": _ep_slow,
                         "slow_min": _ep_slow_min, "slow_max": _ep_slow_max}
                lines.append(_json.dumps(entry))
            try:
                with open(batch_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self._update_batch_indicator()
                count = len(urls)
                self._dl_status.setText(
                    f"✅ {count} URL(s) added to batch — click Run Batch to download")
                self._dl_status.setStyleSheet(
                    f"color:{C['green']};background:{C['surface']};padding:8px;"
                    f"border:1px solid {C['border']};border-radius:3px;")
            except Exception as _be:
                self._dl_status.setText(f"⚠  Batch write error: {_be}")
            return

        slow = False
        slow_min, slow_max = 10, 60
        if hasattr(self, '_opts_slow') and self._opts_slow.isChecked():
            slow = True
            try: slow_min = max(1, int(self._opts_slow_min.text()))
            except ValueError: pass
            try: slow_max = max(slow_min, int(self._opts_slow_max.text()))
            except ValueError: pass
        elif hasattr(self, '_url_slow') and self._url_slow.isChecked():
            slow = True
            try: slow_min = max(1, int(self._url_slow_min.text()))
            except ValueError: pass
            try: slow_max = max(slow_min, int(self._url_slow_max.text()))
            except ValueError: pass
        episode_list = [
            ([uv_exe, "run", "--no-sync", "envied", "dl"] + extra + [service_name, url],
             str(self.install_dir), slow, slow_min, slow_max)
            for url in urls
        ]
        _launch_all_powershell(episode_list)

    def _resolve_uv(self) -> str:
        """Find uv executable path."""
        try:
            cfg = load_config()
            saved = cfg.get("uv_exe") or ""
            if saved and Path(saved).exists():
                return saved
        except Exception:
            pass
        venv_scripts = self.install_dir / ".venv" / "Scripts"
        p = venv_scripts / "uv.exe"
        if p.exists():
            return str(p)
        import shutil as _sh
        hit = _sh.which("uv")
        if hit:
            return hit
        for d in [
            Path(os.path.expanduser("~")) / ".local" / "bin",
            Path(os.environ.get("APPDATA", "")) / "uv" / "bin",
        ]:
            if (d / "uv.exe").exists():
                return str(d / "uv.exe")
        return "uv"

    # ── Download panel slots ──────────────────────────────────────────────────
    def _dl_append_line(self, line: str):
        self._dl_term.write_text(line)

    def _dl_update_progress(self, _pct: int):
        pass  # progress shown in terminal; bar removed from UI

    def _dl_update_episode(self, label: str):
        self._dl_ep_label.setText(label)

    def _dl_update_status(self, _text: str):
        pass  # status shown in terminal; label removed from UI

    def _dl_finished(self, success: bool):
        # Turn Batch Mode off after a run so it doesn't stay on by mistake
        if hasattr(self, '_batch_slider') and self._batch_slider.value() == 1:
            self._batch_slider.setValue(0)

        self._dl_cancel_btn.setText("✓  Close")
        try:
            self._dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._dl_cancel_btn.clicked.connect(self._dl_close_panel)
        if success:
            self._dl_ep_label.setText("✓  All downloads complete! — Click Close to start a new download.")
            self._dl_ep_label.setStyleSheet(
                f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
            self._dl_progress.setValue(100)
        else:
            self._dl_ep_label.setText("Download stopped. — Click Close to start a new download.")
            self._dl_ep_label.setStyleSheet(
                f"color:{C['red']};font-size:13px;font-weight:bold;border:none;")

    def _cleanup_temp_files(self):
        """Remove temp segment files left by N_m3u8DL-RE after a cancelled download."""
        import shutil as _shutil, time as _time, threading as _threading, subprocess as _sp

        # All directories that might contain N_m3u8DL-RE temp files.
        # config.directories.temp resolves to TwinVine/Temp (the CWD-relative "Temp"
        # dir used as --tmp-dir for N_m3u8DL-RE).  Also check _dl_cwd/Temp as fallback.
        candidates = []
        candidates.append(self.install_dir / "Temp")
        if getattr(self, '_dl_cwd', None):
            p = Path(self._dl_cwd) / "Temp"
            if p not in candidates:
                candidates.append(p)

        def _kill_nm3u8():
            try:
                si = _sp.STARTUPINFO()
                si.dwFlags |= _sp.STARTF_USESHOWWINDOW
                si.wShowWindow = _sp.SW_HIDE
                _sp.run(["taskkill", "/F", "/IM", "N_m3u8DL-RE.exe"],
                        startupinfo=si, capture_output=True, timeout=5)
            except Exception:
                pass

        def _wipe():
            for temp_dir in candidates:
                if not temp_dir.is_dir():
                    continue
                for item in list(temp_dir.iterdir()):
                    try:
                        if item.is_dir():
                            _shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                    except Exception:
                        pass

        def _do_cleanup():
            # Kill N_m3u8DL-RE immediately then watch the Temp folders for up to
            # 60 seconds, deleting any file or directory that appears.  This handles
            # the case where N_m3u8DL-RE survives the initial kill and keeps writing
            # segments or completes a merge before finally dying.
            _kill_nm3u8()
            deadline = _time.monotonic() + 60
            consecutive_empty = 0
            while _time.monotonic() < deadline:
                _time.sleep(1)
                _kill_nm3u8()  # keep hammering until it's gone
                any_content = any(
                    any(d.iterdir()) for d in candidates if d.is_dir()
                )
                if any_content:
                    _wipe()
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                    if consecutive_empty >= 5:
                        break  # Temp has been empty for 5 s — done

        _threading.Thread(target=_do_cleanup, daemon=True).start()

    def _dl_handle_prompt(self, prompt_text: str):
        """Show an input dialog when a service needs interactive input (e.g. TVNZ OTP)."""
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        from PyQt6.QtCore import QTimer
        text, ok = QInputDialog.getText(
            self,
            "Input Required",
            prompt_text,
            QLineEdit.EchoMode.Normal,
        )
        proc = getattr(self, '_dl_proc', None)
        if proc and proc.stdin:
            otp = text.strip() if ok else ""
            # Windows PTY (winpty/ConPTY) needs CR (\r) not LF (\n) to
            # trigger the console line-input completion for input() calls.
            # Send both to cover all cases.
            answer = otp + "\r\n"
            try:
                proc.stdin.write(answer.encode())
            except Exception:
                pass
            # Manually write the OTP into the terminal so it appears after
            # "Enter OTP code: " as if typed, then move to a clean line.
            # The PTY echo will land in the wrong place (due to Rich cursor
            # position); we erase that corrupted line 200ms after the echo lands.
            if otp:
                self._dl_term.write_bytes(otp.encode() + b"\r\n")
            QTimer.singleShot(200, lambda: self._dl_term.write_bytes(b"\r\x1b[2K"))

    def _dl_close_panel(self):
        try:
            job = getattr(self, '_dl_job', None)
            if job:
                ctypes.windll.kernel32.CloseHandle(job)
                self._dl_job = None
        except Exception:
            pass
        self._dl_panel.setVisible(False)
        self._dl_proc = None
        self._dl_status_label.setVisible(False)
        try:
            self._dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._dl_status.setText("✓ Ready — click another service to continue")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    def _dl_cancel(self):
        """Cancel the running download."""
        self._dl_cancelled = True  # stops the ticker immediately
        if self._dl_proc and self._dl_proc.poll() is None:
            pid = self._dl_proc.pid
            try:
                self._dl_proc.terminate()
            except Exception:
                pass
            # Close the Job Object handle
            try:
                job = getattr(self, '_dl_job', None)
                if job:
                    ctypes.windll.kernel32.CloseHandle(job)
                    self._dl_job = None
            except Exception:
                pass
            try:
                import subprocess as _sp
                si = _sp.STARTUPINFO()
                si.dwFlags |= _sp.STARTF_USESHOWWINDOW
                si.wShowWindow = _sp.SW_HIDE
                # Kill the envied process tree by PID
                _sp.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    startupinfo=si,
                    capture_output=True,
                    timeout=10,
                )
                # N_m3u8DL-RE detaches from the process tree so kill it by name too
                _sp.run(
                    ["taskkill", "/F", "/IM", "N_m3u8DL-RE.exe"],
                    startupinfo=si,
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
            # Wait for the process to actually exit so the pipe closes
            # and the read loop unblocks cleanly
            try:
                self._dl_proc.wait(timeout=5)
            except Exception:
                pass
        self._dl_proc = None
        # Show cancellation message in the terminal before tearing down the panel
        try:
            self._dl_term.write_text("\r\n\x1b[33mDownload cancelled.\x1b[0m")
        except Exception:
            pass
        # Clean up any temp segment files left behind by N_m3u8DL-RE
        self._cleanup_temp_files()
        self._dl_signals.done.emit(False)

    def _on_service_done(self):
        if getattr(self, '_dl_panel', None) and self._dl_panel.isVisible():
            return  # a download is in progress — keep showing Busy
        self._dl_status.setText("✓ Ready — click another service to continue")
        self._dl_status.setStyleSheet(
            f"color:{C['green']};background:{C['surface']};padding:8px;"
            f"border:1px solid {C['border']};border-radius:3px;")

    # ── Install page ─────────────────────────────────────────────────────────

    def _build_extended_page(self) -> QWidget:
        """Extended Services page — direct envied access for additional platforms."""

        SERVICES = [
            # Tested — known to work
            ("BLAZ",        "BLAZE TV",     0),
            ("NFBC",        "NFBC",         0),
            ("RTDE",        "RTL+",         0),
            ("NPO",       "NPO",            0),
            ("ARD",       "ARD Mediathek",  0),
            ("NRK",       "NRK",            0),
        ]

        TIER_COLOURS = {0: C["green"]}
        TIER_LABELS  = {
            0: "🟢 Supported — works with the app; some services require login credentials or cookies",
        }

        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QLabel(
            "Extended Services  "
            f"<span style='font-size:11px;color:{C['subtext']}'>(Experimental)</span>"
        )
        hdr.setStyleSheet(f"color:{C['text']};font-size:16px;font-weight:bold;")
        hdr.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(hdr)

        note = QLabel(
            "Services not available on the main page. Paste a URL, select a service "
            "and click <b>Download</b> for movies/single episodes, or "
            "<b>Browse Series</b> to pick episodes from a TV series."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        outer.addWidget(note)

        legend_row = QHBoxLayout()
        for tier, lbl in TIER_LABELS.items():
            l = QLabel(lbl)
            l.setStyleSheet(f"color:{TIER_COLOURS[tier]};font-size:10px;")
            legend_row.addWidget(l)
        legend_row.addStretch()
        outer.addLayout(legend_row)

        # ── Service button grid ───────────────────────────────────────────────
        svc_frame = QFrame()
        svc_frame.setObjectName("extSvcFrame")
        svc_frame.setStyleSheet(
            f"QFrame#extSvcFrame{{border:1px solid {C['border']};border-radius:4px;"
            f"background:{C['surface']};}}")
        svc_frame_layout = QVBoxLayout(svc_frame)
        svc_frame_layout.setContentsMargins(4, 4, 4, 4)

        scroll_w = QWidget()
        scroll_w.setStyleSheet("background:transparent;")
        grid = QGridLayout(scroll_w)
        grid.setSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)

        self._ext_selected_service = None
        self._ext_service_btns = {}

        COLS = 6
        for i, (tag, label, tier) in enumerate(SERVICES):
            row, col = divmod(i, COLS)
            btn = QPushButton(label)
            colour = TIER_COLOURS[tier]
            btn.setStyleSheet(
                f"QPushButton{{background:{C['surface']};color:{colour};"
                f"border:1px solid {colour};border-radius:3px;"
                f"padding:5px 4px;font-size:11px;}}"
                f"QPushButton:hover{{background:{colour};color:{C['bg']};}}"
                f"QPushButton:checked{{background:{colour};color:{C['bg']};"
                f"font-weight:bold;}}"
            )
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, t=tag, b=btn:
                                self._ext_select_service(t, b))
            grid.addWidget(btn, row, col)
            self._ext_service_btns[tag] = btn

        svc_frame_layout.addWidget(scroll_w)

        scroll = QScrollArea()
        scroll.setWidget(svc_frame)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(115)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}"
                             "QScrollArea > QWidget > QWidget{border:none;background:transparent;}")
        outer.addWidget(scroll)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{C['border']};")
        outer.addWidget(sep2)

        # ── URL + options ─────────────────────────────────────────────────────
        url_row = QHBoxLayout()
        url_lbl = QLabel("URL:")
        url_lbl.setFixedWidth(36)
        url_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        url_row.addWidget(url_lbl)
        self._ext_url = QLineEdit()
        self._ext_url.setPlaceholderText("Paste episode or series URL here…")
        self._ext_url.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:3px;padding:5px 8px;font-size:12px;")
        url_row.addWidget(self._ext_url)
        outer.addLayout(url_row)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        q_lbl = QLabel("Quality:")
        q_lbl.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        opts_row.addWidget(q_lbl)
        self._ext_quality = QComboBox()
        self._ext_quality.addItems(["Best available", "2160p", "1080p", "720p"])
        self._ext_quality.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:1px solid {C['border']};"
            f"padding:3px 6px;border-radius:3px;font-size:11px;")
        opts_row.addWidget(self._ext_quality)
        ext_chk_style = (
            f"QCheckBox{{color:{C['text']};font-size:11px;font-weight:bold;}}"
            f"QCheckBox::indicator:unchecked{{border:1px solid {C['subtext']};}}"
        )
        self._ext_no_subs = QCheckBox("No subtitles")
        self._ext_no_subs.setStyleSheet(ext_chk_style)
        opts_row.addWidget(self._ext_no_subs)
        self._ext_slow = QCheckBox("Slow mode")
        self._ext_slow.setStyleSheet(ext_chk_style)
        opts_row.addWidget(self._ext_slow)
        opts_row.addStretch()
        outer.addLayout(opts_row)

        # Action buttons
        action_row = QHBoxLayout()
        self._ext_download_btn = QPushButton("✓  Download")
        self._ext_download_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:7px 18px;border-radius:3px;")
        self._ext_download_btn.clicked.connect(self._ext_do_download)
        action_row.addWidget(self._ext_download_btn)
        self._ext_fetch_btn = QPushButton("🔍  Fetch Tracks")
        self._ext_fetch_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:7px 18px;border-radius:3px;")
        self._ext_fetch_btn.clicked.connect(self._ext_do_fetch_tracks)
        self._ext_browse_btn = QPushButton("📋  Browse Series")
        self._ext_browse_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:7px 18px;border-radius:3px;")
        self._ext_browse_btn.clicked.connect(self._ext_do_browse)
        action_row.addWidget(self._ext_browse_btn)
        action_row.addStretch()
        action_row.addWidget(self._ext_fetch_btn)
        outer.addLayout(action_row)

        self._ext_status = QLabel("")
        self._ext_status.setWordWrap(True)
        self._ext_status.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        outer.addWidget(self._ext_status)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"color:{C['border']};")
        outer.addWidget(sep3)

        # ── Download log panel (always visible, above pickers) ────────────────
        self._ext_season_panel = QFrame()
        self._ext_season_panel.setObjectName('extSeasonPanel')
        self._ext_season_panel.setStyleSheet(
            f"QFrame#extSeasonPanel{{background:{C['surface']};"
            f"border:1px solid {C['green']};border-radius:6px;}}")
        self._ext_season_panel.setVisible(False)
        sp_layout = QVBoxLayout(self._ext_season_panel)
        sp_layout.setContentsMargins(12, 10, 12, 10)
        sp_layout.setSpacing(6)

        sp_hdr_row = QHBoxLayout()
        sp_title = QLabel("Select Seasons")
        sp_title.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        sp_hdr_row.addWidget(sp_title)
        sp_hdr_row.addStretch()
        sp_all = QPushButton("All")
        sp_all.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};border:none;"
            f"padding:3px 10px;border-radius:3px;font-size:11px;")
        sp_none = QPushButton("None")
        sp_none.setStyleSheet(sp_all.styleSheet())
        sp_hdr_row.addWidget(sp_all)
        sp_hdr_row.addWidget(sp_none)
        sp_layout.addLayout(sp_hdr_row)

        sp_scroll = QScrollArea()
        sp_scroll.setWidgetResizable(True)
        sp_scroll.setMinimumHeight(160)
        sp_scroll.setMaximumHeight(300)
        sp_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        sp_scroll_w = QWidget()
        sp_scroll_w.setStyleSheet("background:transparent;")
        self._ext_season_list = QVBoxLayout(sp_scroll_w)
        self._ext_season_list.setSpacing(3)
        self._ext_season_list.addStretch()
        sp_scroll.setWidget(sp_scroll_w)
        sp_layout.addWidget(sp_scroll)

        sp_btn_row = QHBoxLayout()
        sp_next = QPushButton("✓  Confirm")
        sp_next.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 18px;border-radius:3px;")
        sp_cancel = QPushButton("✕  Cancel")
        sp_cancel.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 14px;border-radius:3px;")
        sp_btn_row.addWidget(sp_next)
        sp_btn_row.addWidget(sp_cancel)
        sp_btn_row.addStretch()
        sp_layout.addLayout(sp_btn_row)
        outer.addWidget(self._ext_season_panel, 1)

        # ── Episode picker (Screen 2) ─────────────────────────────────────────
        self._ext_ep_panel = QFrame()
        self._ext_ep_panel.setObjectName('extEpPanel')
        self._ext_ep_panel.setStyleSheet(
            f"QFrame#extEpPanel{{background:{C['surface']};"
            f"border:1px solid {C['green']};border-radius:6px;}}")
        self._ext_ep_panel.setVisible(False)
        ep_layout = QVBoxLayout(self._ext_ep_panel)
        ep_layout.setContentsMargins(12, 10, 12, 10)
        ep_layout.setSpacing(6)

        ep_hdr_row = QHBoxLayout()
        self._ext_ep_title = QLabel("Select Episodes")
        self._ext_ep_title.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        ep_hdr_row.addWidget(self._ext_ep_title)
        ep_hdr_row.addStretch()
        ep_all = QPushButton("All")
        ep_all.setStyleSheet(sp_all.styleSheet())
        ep_none = QPushButton("None")
        ep_none.setStyleSheet(sp_all.styleSheet())
        ep_hdr_row.addWidget(ep_all)
        ep_hdr_row.addWidget(ep_none)
        ep_layout.addLayout(ep_hdr_row)

        ep_scroll = QScrollArea()
        ep_scroll.setWidgetResizable(True)
        ep_scroll.setMinimumHeight(300)
        ep_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        ep_scroll_w = QWidget()
        ep_scroll_w.setStyleSheet("background:transparent;")
        self._ext_ep_list = QVBoxLayout(ep_scroll_w)
        self._ext_ep_list.setSpacing(3)
        self._ext_ep_list.addStretch()
        ep_scroll.setWidget(ep_scroll_w)
        ep_layout.addWidget(ep_scroll)

        ep_btn_row = QHBoxLayout()
        ep_back = QPushButton("◀  Back")
        ep_back.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};"
            f"border:none;padding:6px 14px;border-radius:3px;")
        ep_confirm = QPushButton("✓  Download Selected")
        ep_confirm.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"border:none;padding:6px 18px;border-radius:3px;")
        ep_cancel2 = QPushButton("✕  Cancel")
        ep_cancel2.setStyleSheet(sp_cancel.styleSheet())
        ep_btn_row.addWidget(ep_back)
        ep_btn_row.addWidget(ep_confirm)
        ep_btn_row.addWidget(ep_cancel2)
        ep_btn_row.addStretch()
        ep_layout.addLayout(ep_btn_row)
        outer.addWidget(self._ext_ep_panel)

        # ── Download log panel (hidden until a download or fetch starts) ─────
        self._ext_dl_panel = QFrame()
        self._ext_dl_panel.setObjectName('extDlPanel')
        self._ext_dl_panel.setStyleSheet(
            f"QFrame#extDlPanel{{background:{C['surface']};"
            f"border:1px solid {C['border']};border-radius:6px;}}")
        self._ext_dl_panel.setVisible(False)
        dl_layout = QVBoxLayout(self._ext_dl_panel)
        dl_layout.setContentsMargins(12, 10, 12, 10)
        dl_layout.setSpacing(6)

        self._ext_ep_label = QLabel("")
        self._ext_ep_label.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        dl_layout.addWidget(self._ext_ep_label)

        self._ext_term = _TermView(C['bg'], C['subtext'], scroll_thumb=C['border'])
        self._ext_term.setMinimumHeight(420)
        dl_layout.addWidget(self._ext_term, stretch=1)

        dl_btn_row = QHBoxLayout()
        self._ext_dl_cancel_btn = QPushButton("✕  Cancel Download")
        self._ext_dl_cancel_btn.setStyleSheet(sp_cancel.styleSheet())
        dl_btn_row.addWidget(self._ext_dl_cancel_btn)
        dl_btn_row.addStretch()
        dl_layout.addLayout(dl_btn_row)
        outer.addWidget(self._ext_dl_panel)

        outer.addStretch()

        # ── Wire up season/episode picker events ──────────────────────────────
        # Store parsed seasons data when Browse is called
        self._ext_seasons_data = []   # [(season_num, [(ep_num, ep_name)])]
        self._ext_season_cbs  = []    # season checkboxes
        self._ext_ep_cbs      = []    # episode checkboxes with (s_num, ep_num)
        self._ext_dl_proc_ref = [None]

        def _ext_show_season_panel(seasons_data):
            self._ext_seasons_data = seasons_data
            self._ext_season_panel.setVisible(True)
            self._ext_ep_panel.setVisible(False)

            # Clear and rebuild season list
            while self._ext_season_list.count():
                item = self._ext_season_list.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            self._ext_season_cbs = []
            chk_style = (
                "QCheckBox {"
                f"color:{C['text']};font-size:12px;padding:5px 8px;"
                f"border:1px solid {C['border']};border-radius:3px;"
                f"background:{C['bg']};}}"
                "QCheckBox:hover {"
                f"background:{C['surface']};}}"
                "QCheckBox::indicator {"
                "width:14px;height:14px;border-radius:2px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                "QCheckBox::indicator:checked {"
                f"background:{C['green']};border:2px solid {C['green']};}}"
            )
            for s_num, eps in seasons_data:
                cb = QCheckBox(f"Season {s_num}  ({len(eps)} episode{'s' if len(eps) != 1 else ''})")
                cb.setStyleSheet(chk_style)
                self._ext_season_list.addWidget(cb)
                self._ext_season_cbs.append((s_num, cb))
            self._ext_season_list.addStretch()

        def _ext_show_ep_panel():
            selected_seasons = [(s, eps) for s, eps in self._ext_seasons_data
                                if any(cb.isChecked()
                                       for sn, cb in self._ext_season_cbs
                                       if sn == s)]
            if not selected_seasons:
                self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;")
                self._ext_status.setText("⚠  Please select at least one season.")
                return

            self._ext_season_panel.setVisible(False)
            self._ext_ep_panel.setVisible(True)

            # Clear episode list
            while self._ext_ep_list.count():
                item = self._ext_ep_list.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            self._ext_ep_cbs = []
            chk_style = (
                f"QCheckBox{{color:{C['text']};font-size:12px;padding:3px 6px;}}"
                f"QCheckBox::indicator{{width:14px;height:14px;"
                f"border:2px solid {C['subtext']};background:{C['bg']};}}"
                f"QCheckBox::indicator:checked{{background:{C['green']};"
                f"border:2px solid {C['green']};}}"
            )
            hdr_style = (
                f"QCheckBox{{color:{C['yellow']};font-size:12px;font-weight:bold;"
                f"padding:5px 6px;border-top:1px solid {C['border']};}}"
                f"QCheckBox::indicator{{width:14px;height:14px;"
                f"border:2px solid {C['yellow']};background:{C['bg']};}}"
                f"QCheckBox::indicator:checked{{background:{C['yellow']};"
                f"border:2px solid {C['yellow']};}}"
            )

            for s_num, eps in selected_seasons:
                # Season header checkbox
                hdr_cb = QCheckBox(f"Season {s_num}")
                hdr_cb.setStyleSheet(hdr_style)
                self._ext_ep_list.addWidget(hdr_cb)
                ep_cb_list = []
                for ep_num, ep_name in eps:
                    ep_cb = QCheckBox(f"  E{ep_num:02d}. {ep_name}")
                    ep_cb.setStyleSheet(chk_style)
                    ep_cb.setProperty("s_num", s_num)
                    ep_cb.setProperty("ep_num", ep_num)
                    self._ext_ep_list.addWidget(ep_cb)
                    ep_cb_list.append(ep_cb)
                    self._ext_ep_cbs.append(ep_cb)
                # Header toggles all its episodes
                def _make_toggle(cbs):
                    def _t(checked): [cb.setChecked(checked) for cb in cbs]
                    return _t
                hdr_cb.toggled.connect(_make_toggle(ep_cb_list))

            self._ext_ep_list.addStretch()

            total = sum(len(e) for _, e in selected_seasons)
            self._ext_ep_title.setText(
                f"Select Episodes  ({total} available)")

        def _ext_do_confirm():
            selected = [(cb.property("s_num"), cb.property("ep_num"))
                        for cb in self._ext_ep_cbs if cb.isChecked()]
            if not selected:
                self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;")
                self._ext_status.setText("⚠  Please select at least one episode.")
                return
            self._ext_ep_panel.setVisible(False)
            self._ext_status.setStyleSheet(f"color:{C['green']};font-size:11px;")
            self._ext_status.setText(f"⏳  Downloading {len(selected)} episode(s)…")
            # BrowseWorker services (NRK, ARD, ZDF) have individual episode URLs
            ep_urls = getattr(self, '_ext_browse_ep_urls', None)
            if ep_urls:
                urls = [ep_urls[s_e] for s_e in sorted(selected) if s_e in ep_urls]
                if urls:
                    self._ext_start_worker_queue(urls)
            else:
                wanted = ",".join(f"S{s:02d}E{e:02d}" for s, e in sorted(selected))
                self._ext_start_worker(extra_wanted=wanted)

        def _ext_cancel_picker():
            self._ext_season_panel.setVisible(False)
            self._ext_ep_panel.setVisible(False)
            self._ext_status.setText("")

        sp_all.clicked.connect(
            lambda: [cb.setChecked(True) for _, cb in self._ext_season_cbs])
        sp_none.clicked.connect(
            lambda: [cb.setChecked(False) for _, cb in self._ext_season_cbs])
        sp_next.clicked.connect(_ext_show_ep_panel)
        sp_cancel.clicked.connect(_ext_cancel_picker)

        ep_all.clicked.connect(
            lambda: [cb.setChecked(True) for cb in self._ext_ep_cbs])
        ep_none.clicked.connect(
            lambda: [cb.setChecked(False) for cb in self._ext_ep_cbs])
        ep_back.clicked.connect(
            lambda: (self._ext_ep_panel.setVisible(False),
                     self._ext_season_panel.setVisible(True)))
        ep_confirm.clicked.connect(_ext_do_confirm)
        ep_cancel2.clicked.connect(_ext_cancel_picker)

        # Store callbacks for use by _ext_do_browse
        self._ext_show_season_panel = _ext_show_season_panel

        return w

    def _ext_select_service(self, tag: str, btn: QPushButton):
        """Toggle service button selection."""
        # Deselect all others
        for t, b in self._ext_service_btns.items():
            if t != tag:
                b.setChecked(False)
        self._ext_selected_service = tag if btn.isChecked() else None
        self._ext_status.setText(
            f"Selected: {tag}" if self._ext_selected_service else "")

    def _ext_build_args(self) -> list:
        """Build quality/subtitle args for extended service download."""
        args = []
        q = self._ext_quality.currentText()
        if q != "Best available":
            args += ["-q", q.replace("p", "")]
        if self._ext_no_subs.isChecked():
            args += ["--no-subs"]
        if self._ext_slow.isChecked():
            args += ["--slow"]
        return args

    def _ext_validate(self) -> bool:
        """Check service and URL are set before launching."""
        if not self._ext_selected_service:
            self._ext_status.setStyleSheet(
                f"color:{C['red']};font-size:11px;")
            self._ext_status.setText("⚠  Please select a service first.")
            return False
        url = self._ext_url.text().strip()
        if not url or not url.startswith("http"):
            self._ext_status.setStyleSheet(
                f"color:{C['red']};font-size:11px;")
            self._ext_status.setText("⚠  Please paste a valid URL.")
            return False
        return True

    def _ext_do_download(self):
        """Direct download — no episode picker."""
        if not self._ext_validate():
            return
        self._ext_season_panel.setVisible(False)
        self._ext_ep_panel.setVisible(False)
        self._ext_start_worker()

    def _ext_close_dl_panel(self):
        """Hide the download log panel and reset the cancel button and quality dropdown."""
        self._ext_dl_panel.setVisible(False)
        try:
            self._ext_dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._ext_dl_cancel_btn.setText("✕  Cancel Download")
        # Reset quality back to the standard options in case fetch tracks changed it.
        self._ext_quality.clear()
        self._ext_quality.addItems(["Best available", "2160p", "1080p", "720p"])
        # Clear the status/label messages set by fetch tracks.
        self._ext_status.setText("")
        self._ext_ep_label.setText("")

    def _ext_do_fetch_tracks(self):
        """Run envied dl --list to show available tracks without downloading."""
        if not self._ext_validate():
            return
        url = self._ext_resolve_url(self._ext_url.text().strip())
        uv = self.cfg.get("uv_exe") or find_uv()
        if not uv:
            self._ext_status.setText("⚠  uv not found. Run Install first.")
            return
        svc = self._ext_selected_service
        self._ext_dl_panel.setVisible(True)
        self._ext_season_panel.setVisible(False)
        self._ext_ep_panel.setVisible(False)
        self._ext_term.reset_terminal()
        self._ext_ep_label.setText("Fetching tracks…")
        self._ext_ep_label.setStyleSheet(
            f"color:{C['yellow']};font-size:13px;font-weight:bold;border:none;")
        try:
            self._ext_dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._ext_dl_cancel_btn.setText("✕  Cancel Download")
        self._ext_dl_cancel_btn.clicked.connect(self._ext_close_dl_panel)
        self._ext_status.setStyleSheet(
            f"color:{C['yellow']};font-size:11px;")
        self._ext_status.setText("⏳  Fetching available tracks…")
        self._ext_fetch_btn.setEnabled(False)

        self._ext_fetch_worker = FetchTracksWorker(uv, self.install_dir, svc, url)
        self._ext_fetch_worker.log_line.connect(self._ext_term.write_text)
        self._ext_fetch_worker.log_line.connect(self._append_log)
        self._ext_fetch_worker.error.connect(self._ext_fetch_tracks_error)
        self._ext_fetch_worker.finished.connect(self._ext_fetch_tracks_done)
        self._ext_fetch_worker.start()

    def _ext_fetch_tracks_error(self, err: str):
        self._ext_status.setText(f"Error: {err}")
        self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;")
        self._ext_fetch_btn.setEnabled(True)

    def _ext_fetch_tracks_done(self, output: str):
        import re as _re
        qualities = ["Best available"]
        seen_q = set()
        for m in _re.finditer(r'\|\s*\d+x(\d+)\s*@', output):
            h = int(m.group(1))
            label = f"{h}p"
            if label not in seen_q:
                qualities.append(label)
                seen_q.add(label)
        self._ext_quality.clear()
        self._ext_quality.addItems(qualities)
        if len(qualities) > 1:
            q_str = ", ".join(qualities[1:])
            self._ext_ep_label.setText("✓  Tracks fetched")
            self._ext_ep_label.setStyleSheet(
                f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
            self._ext_status.setText(
                f"✓ Available resolutions: {q_str}. Select your preference then click Download.")
            self._ext_status.setStyleSheet(f"color:{C['green']};font-size:11px;")
        else:
            self._ext_ep_label.setText("Could not parse track info")
            self._ext_ep_label.setStyleSheet(
                f"color:{C['yellow']};font-size:13px;font-weight:bold;border:none;")
            self._ext_status.setText(
                "Could not parse track info — see the log below for full output.")
            self._ext_status.setStyleSheet(f"color:{C['yellow']};font-size:11px;")
        try:
            self._ext_dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._ext_dl_cancel_btn.setText("✓  Close")
        self._ext_dl_cancel_btn.clicked.connect(self._ext_close_dl_panel)
        self._ext_fetch_btn.setEnabled(True)

    def _ext_do_browse(self):
        """Fetch title list then show season picker."""
        if not self._ext_validate():
            return
        url = self._ext_url.text().strip()
        uv  = self.cfg.get("uv_exe") or find_uv()
        if not uv:
            self._ext_status.setText("⚠  uv not found. Run Install first.")
            return
        self._ext_status.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        self._ext_status.setText("⏳  Fetching series info…")
        self._ext_season_panel.setVisible(False)
        self._ext_ep_panel.setVisible(False)
        self._ext_browse_ep_urls = None  # reset any prior BrowseWorker state

        # ARD browse is not reliably supported — direct URL only
        if self._ext_selected_service == "ARD":
            self._ext_status.setStyleSheet(f"color:{C['yellow']};font-size:11px;")
            self._ext_status.setText(
                "⚠  ARD browse is not supported — paste a direct episode URL and use Download.")
            return

        # Services where envied can't handle series URLs — use BrowseWorker instead
        _BROWSE_WORKER_SERVICES = {"NRK", "ZDF"}
        if self._ext_selected_service in _BROWSE_WORKER_SERVICES:
            self._ext_browse_worker = BrowseWorker(self._ext_selected_service, url)

            def _on_browse_done(episodes):
                # episodes: [{series_no, title, url, synopsis}, ...]
                # Build seasons dict: {s_num: [(ep_num, ep_label), ...]}
                import re as _re2
                seasons_dict = {}
                ep_urls = {}
                for ep in episodes:
                    try:
                        s_n = int(ep.get("series_no") or 1)
                    except (TypeError, ValueError):
                        s_n = 1
                    title = ep.get("title") or ""
                    ep_url = ep.get("url") or ""
                    # Extract episode number from title "S01E04 Name" or fallback
                    m = _re2.match(r'S\d+E(\d+)', title)
                    e_n = int(m.group(1)) if m else (len(seasons_dict.get(s_n, [])) + 1)
                    label = _re2.sub(r'^S\d+E\d+\s*', '', title) or title
                    seasons_dict.setdefault(s_n, []).append((e_n, label))
                    ep_urls[(s_n, e_n)] = ep_url
                seasons = sorted(seasons_dict.items())
                self._ext_browse_ep_urls = ep_urls
                if not seasons:
                    self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;")
                    self._ext_status.setText("⚠  No seasons found. Try Download instead.")
                    return
                self._ext_status.setText("")
                self._ext_show_season_panel(seasons)

            def _on_browse_error(msg):
                self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;")
                self._ext_status.setText(f"⚠  {msg}")

            self._ext_browse_worker.done.connect(_on_browse_done)
            self._ext_browse_worker.error.connect(_on_browse_error)
            self._ext_browse_worker.status.connect(
                lambda s: self._ext_status.setText(f"⏳  {s}"))
            self._ext_browse_worker.start()
            return

        import subprocess as _sp2, re as _re2, os as _os2, threading as _th2
        from PyQt6.QtCore import QObject, pyqtSignal as _pysig

        # Signal dispatcher — created on main thread so signals are queued back here
        class _Dispatch(QObject):
            _call = _pysig(object)
            def __init__(self):
                super().__init__()
                self._call.connect(lambda fn: fn())
            def post(self, fn):
                self._call.emit(fn)

        _dispatch = _Dispatch()

        def _fetch():
            try:
                env = _os2.environ.copy()
                env["NO_COLOR"]         = "1"
                env["TERM"]             = "dumb"
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUNBUFFERED"] = "1"
                venv_scripts = str(self.install_dir / ".venv" / "Scripts")
                env["PATH"] = venv_scripts + ";" + r"C:\Tools\bin" + ";" + env.get("PATH", "")
                svc_url = self._ext_resolve_url(url)
                cmd = ([str(uv), "run", "--no-sync", "envied", "dl"]
                       + self._ext_build_args()
                       + ["--list-titles", self._ext_selected_service, svc_url])
                r = _sp2.run(
                    cmd, cwd=str(self.install_dir),
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    env=env, timeout=60,
                    creationflags=_sp2.CREATE_NO_WINDOW,
                )
                output = r.stdout + (r.stderr or "")

                season_re   = _re2.compile(r'Season\s+(\d+)\s*:')
                episode_re  = _re2.compile(r'(\d+)\.\s+(.+?)\s*$')
                episode_re2 = _re2.compile(r'Episode\s+(\d+)\s*$')
                seasons = []
                current_season = None
                current_eps = []
                for line in output.splitlines():
                    stripped = line.strip()
                    sm = season_re.search(stripped)
                    if sm:
                        if current_season is not None:
                            seasons.append((current_season, current_eps))
                        current_season = int(sm.group(1))
                        current_eps = []
                        continue
                    if current_season is not None:
                        em = episode_re.search(stripped)
                        if em:
                            current_eps.append((int(em.group(1)), em.group(2).strip()))
                            continue
                        em2 = episode_re2.search(stripped)
                        if em2:
                            n = int(em2.group(1))
                            current_eps.append((n, f"Episode {n}"))
                if current_season is not None:
                    seasons.append((current_season, current_eps))

                def _show():
                    if not seasons:
                        self._ext_status.setStyleSheet(
                            f"color:{C['red']};font-size:11px;")
                        self._ext_status.setText(
                            "⚠  No seasons found. Try Download instead.")
                        return
                    self._ext_status.setText("")
                    self._ext_show_season_panel(seasons)

                _dispatch.post(_show)

            except Exception as e:
                _msg = str(e)
                _dispatch.post(lambda: (
                    self._ext_status.setStyleSheet(f"color:{C['red']};font-size:11px;"),
                    self._ext_status.setText(f"⚠  {_msg}"),
                ))

        _th2.Thread(target=_fetch, daemon=True).start()

    def _ext_resolve_url(self, url: str) -> str:
        """Return the service-appropriate URL/slug for the selected service."""
        # RKTN: pass URL through as-is — the service code parses it itself
        # and uses "movies" in the URL to detect movie vs TV show
        return url

    def _ext_start_worker(self, extra_wanted=None):
        """Launch the ExtendedServiceWorker for the current service/URL/args."""
        import re as _re2
        raw = self._ext_url.text().strip()
        # Strip any trailing -w / --wanted argument the user appended to the URL.
        # e.g. "https://.../.../show -w s01e02"  →  url="https://...", wanted="s01e02"
        _w_match = _re2.search(r'\s+(?:-w|--wanted)\s+(\S+)\s*$', raw, _re2.IGNORECASE)
        if _w_match and not extra_wanted:
            extra_wanted = _w_match.group(1)
            raw = raw[:_w_match.start()].strip()
        url = self._ext_resolve_url(raw)
        uv  = self.cfg.get("uv_exe") or find_uv()
        if not uv:
            self._ext_status.setText("⚠  uv not found.")
            return

        extra = self._ext_build_args()
        if extra_wanted:
            extra += ["--wanted", extra_wanted]

        self._ext_dl_panel.setVisible(True)
        self._ext_season_panel.setVisible(False)
        self._ext_ep_panel.setVisible(False)
        self._ext_term.reset_terminal()
        self._ext_ep_label.setText(f"Downloading from {self._ext_selected_service}…")
        self._ext_ep_label.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")

        self._ext_worker = ExtendedServiceWorker(
            uv_exe      = uv,
            install_dir = self.install_dir,
            service     = self._ext_selected_service,
            url         = url,
            extra_args  = extra,
            select_titles = False,
        )

        def _on_done(success):
            self._ext_status.setText("")
            if success:
                self._ext_ep_label.setText(
                    "✓  All downloads complete! — Click Close to start a new download.")
                self._ext_ep_label.setStyleSheet(
                    f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
                self._ext_url.clear()
            else:
                self._ext_ep_label.setText("Download stopped. — Click Close to start a new download.")
                self._ext_ep_label.setStyleSheet(
                    f"color:{C['red']};font-size:13px;font-weight:bold;border:none;")
            try:
                self._ext_dl_cancel_btn.clicked.disconnect()
            except Exception:
                pass
            self._ext_dl_cancel_btn.setText("✓  Close")
            self._ext_dl_cancel_btn.clicked.connect(self._ext_close_dl_panel)

        def _on_cancel():
            if hasattr(self, '_ext_worker'):
                self._ext_worker._cancelled = True
            self._ext_close_dl_panel()
            self._ext_status.setText("Cancelled.")
            # Wipe any partial temp files envied left behind (runs in background).
            import shutil as _shutil, threading as _thr
            _temp = self.install_dir / "Temp"
            def _wipe_ext_temp():
                import time as _t; _t.sleep(1)  # brief wait for process to die
                if _temp.is_dir():
                    for _item in list(_temp.iterdir()):
                        try:
                            if _item.is_dir(): _shutil.rmtree(_item, ignore_errors=True)
                            else: _item.unlink(missing_ok=True)
                        except Exception: pass
            _thr.Thread(target=_wipe_ext_temp, daemon=True).start()

        try:
            self._ext_dl_cancel_btn.clicked.disconnect()
        except Exception:
            pass
        self._ext_dl_cancel_btn.setText("✕  Cancel Download")
        self._ext_dl_cancel_btn.clicked.connect(_on_cancel)

        self._ext_worker.raw_bytes.connect(self._ext_term.write_bytes)
        self._ext_worker.log_line.connect(self._append_log)
        self._ext_worker.done.connect(_on_done)
        self._ext_worker.start()

    def _ext_start_worker_queue(self, urls: list):
        """Run a list of episode URLs sequentially through ExtendedServiceWorker."""
        if not urls:
            return
        remaining = list(urls)
        total = len(remaining)

        def _start_next():
            if not remaining:
                return
            ep_url = remaining.pop(0)
            done_so_far = total - len(remaining)
            uv  = self.cfg.get("uv_exe") or find_uv()
            extra = self._ext_build_args()
            svc = self._ext_selected_service

            self._ext_dl_panel.setVisible(True)
            self._ext_season_panel.setVisible(False)
            self._ext_ep_panel.setVisible(False)
            self._ext_term.reset_terminal()
            self._ext_ep_label.setText(
                f"Downloading {done_so_far} of {total} from {svc}…")
            self._ext_ep_label.setStyleSheet(
                f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")

            worker = ExtendedServiceWorker(
                uv_exe      = uv,
                install_dir = self.install_dir,
                service     = svc,
                url         = ep_url,
                extra_args  = extra,
                select_titles = False,
            )
            self._ext_worker = worker

            def _on_done(success):
                if remaining:
                    _start_next()
                else:
                    if success:
                        self._ext_ep_label.setText(
                            "✓  All downloads complete! — Click Close to start a new download.")
                        self._ext_ep_label.setStyleSheet(
                            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
                        self._ext_url.clear()
                    else:
                        self._ext_ep_label.setText(
                            "Download stopped. — Click Close to start a new download.")
                        self._ext_ep_label.setStyleSheet(
                            f"color:{C['red']};font-size:13px;font-weight:bold;border:none;")
                    try:
                        self._ext_dl_cancel_btn.clicked.disconnect()
                    except Exception:
                        pass
                    self._ext_dl_cancel_btn.setText("✓  Close")
                    self._ext_dl_cancel_btn.clicked.connect(self._ext_close_dl_panel)

            worker.done.connect(_on_done)

            try:
                self._ext_dl_cancel_btn.clicked.disconnect()
            except Exception:
                pass
            self._ext_dl_cancel_btn.setText("✕  Cancel Download")
            def _on_cancel():
                remaining.clear()
                if hasattr(self, '_ext_worker'):
                    self._ext_worker._cancelled = True
                self._ext_close_dl_panel()
                self._ext_status.setText("Cancelled.")
            self._ext_dl_cancel_btn.clicked.connect(_on_cancel)

            worker.raw_bytes.connect(self._ext_term.write_bytes)
            worker.log_line.connect(self._append_log)

            worker.start()

        _start_next()

    def _build_install_page(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)

        hdr = QLabel("Install / Update")
        hdr.setStyleSheet(f"font-size:20px;font-weight:bold;color:{C['green']};")
        layout.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # Directory
        dir_frame = QFrame()
        dir_frame.setStyleSheet(
            f"background:{C['surface']};border:1px solid {C['border']};border-radius:4px;")
        df = QVBoxLayout(dir_frame)
        df.setContentsMargins(14, 12, 14, 12)
        QLabel("Install directory").setStyleSheet(f"color:{C['subtext']};")
        lbl = QLabel("Install directory")
        lbl.setStyleSheet(f"color:{C['subtext']};font-weight:bold;")
        df.addWidget(lbl)
        dir_row = QHBoxLayout()
        self._dir_entry = QLineEdit(str(self.install_dir))
        dir_row.addWidget(self._dir_entry)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        df.addLayout(dir_row)
        layout.addWidget(dir_frame)

        # Warning about install time
        warn = QLabel("⚠ Check the Log tab for a detailed view of the current installation.")
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color:{C['yellow']};font-size:11px;padding:6px 0 2px 0;")
        layout.addWidget(warn)

        # Pointer to help page
        help_note = QLabel(
            "ℹ️  Before installing, see the <b>Help</b> page for full details of what this will do."
        )
        help_note.setWordWrap(True)
        help_note.setStyleSheet(f"color:{C['subtext']};font-size:11px;padding:2px 0 4px 0;")
        layout.addWidget(help_note)

        # Buttons
        btn_row = QHBoxLayout()
        self._install_btn = QPushButton("▶  Install EnvyUI Tools")
        self._install_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};font-weight:bold;"
            f"padding:10px 20px;font-size:13px;border-radius:4px;")
        self._install_btn.clicked.connect(self._start_install)
        btn_row.addWidget(self._install_btn)
        self._update_btn = QPushButton("🔄  Check for Updates")
        self._update_btn.clicked.connect(self._check_updates)
        self._update_btn.setVisible(self._is_installed())
        btn_row.addWidget(self._update_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Build EXE section — only shown when installed
        self._build_frame = QFrame()
        self._build_frame.setStyleSheet(
            f"QFrame#buildFrame{{background:{C['surface']};border:1px solid {C['border']};"
            f"border-radius:4px;}}")
        self._build_frame.setObjectName('buildFrame')
        self._build_frame.setVisible(self._is_installed())
        bf = QVBoxLayout(self._build_frame)
        bf.setContentsMargins(14, 12, 14, 12)
        bf.setSpacing(6)
        build_hdr = QLabel("Build Standalone EXE")
        build_hdr.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;border:none;")
        bf.addWidget(build_hdr)
        build_note = QLabel(
            "Builds EnvyUI.exe — a launcher that uses your existing Python installation "
            "so downloads behave identically to the batch file. "
            "Both EnvyUI.exe and EnvyUI.lnk are saved next to envy_launcher.py.\n\n"
            "• Double-click EnvyUI.exe to launch, or pin it to the Start menu via File Explorer.\n"
            "• To pin to the taskbar: right-click EnvyUI.lnk → Pin to taskbar "
            "(the shortcut is required — pinning the exe directly causes two icons to appear).\n"
            "• Updates only require replacing envy_launcher.py — no need to rebuild the exe."
        )
        build_note.setWordWrap(True)
        build_note.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        bf.addWidget(build_note)
        build_btn_row = QHBoxLayout()
        self._build_btn = QPushButton("🔨  Build EXE")
        self._build_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};font-weight:bold;"
            f"padding:8px 18px;border-radius:4px;border:none;")
        self._build_btn.clicked.connect(self._start_build_exe)
        build_btn_row.addWidget(self._build_btn)
        build_btn_row.addStretch()
        bf.addLayout(build_btn_row)
        self._build_status = QLabel("")
        self._build_status.setWordWrap(True)
        self._build_status.setStyleSheet(f"color:{C['subtext']};font-size:11px;border:none;")
        bf.addWidget(self._build_status)
        layout.addWidget(self._build_frame)

        # ── Settings card (shown only when installed) ─────────────────────────
        self._settings_frame = QFrame()
        self._settings_frame.setStyleSheet(
            f"QFrame#settingsFrame{{background:{C['surface']};border:1px solid {C['border']};border-radius:4px;}}")
        self._settings_frame.setObjectName("settingsFrame")
        self._settings_frame.setVisible(self._is_installed())
        sf2 = QVBoxLayout(self._settings_frame)
        sf2.setContentsMargins(14, 12, 14, 12)
        sf2.setSpacing(8)

        settings_hdr = QLabel("Settings")
        settings_hdr.setStyleSheet(
            f"color:{C['green']};font-size:13px;font-weight:bold;")
        sf2.addWidget(settings_hdr)

        # Downloads location
        dl_lbl = QLabel("Change Downloads Location")
        dl_lbl.setStyleSheet(f"color:{C['subtext']};font-weight:bold;")
        sf2.addWidget(dl_lbl)
        dl_row = QHBoxLayout()
        dl_row.setContentsMargins(0, 0, 0, 6)
        self._dl_dir_entry = QLineEdit()
        self._dl_dir_entry.setPlaceholderText("Default: EnvyCore/Downloads")
        self._dl_dir_entry.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        dl_row.addWidget(self._dl_dir_entry)
        _small_btn = (f"background:{C['overlay']};color:{C['text']};border:none;"
                      f"padding:6px 14px;border-radius:4px;")
        dl_browse_btn = QPushButton("Browse…")
        dl_browse_btn.setStyleSheet(_small_btn)
        dl_browse_btn.clicked.connect(self._browse_dl_dir)
        dl_row.addWidget(dl_browse_btn)
        dl_save_btn = QPushButton("Save")
        dl_save_btn.setStyleSheet(_small_btn)
        dl_save_btn.clicked.connect(self._save_dl_dir)
        dl_row.addWidget(dl_save_btn)
        sf2.addLayout(dl_row)
        self._dl_dir_status = QLabel("")
        self._dl_dir_status.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        sf2.addWidget(self._dl_dir_status)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sf2.addWidget(sep2)

        # Backup settings
        backup_lbl = QLabel("Back up settings")
        backup_lbl.setStyleSheet(f"color:{C['subtext']};font-weight:bold;")
        sf2.addWidget(backup_lbl)
        backup_note = QLabel(
            "Creates a timestamped backup folder on your Desktop containing envied.yaml, "
            "WVDs, PRDs, and Cookies. Click 'Open Location' after backing up to view the folder."
        )
        backup_note.setWordWrap(True)
        backup_note.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        sf2.addWidget(backup_note)
        backup_btn_row = QHBoxLayout()
        backup_btn = QPushButton("💾  Back Up Settings")
        backup_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};font-weight:bold;"
            f"padding:8px 18px;border-radius:4px;border:none;")
        backup_btn.clicked.connect(self._backup_settings)
        backup_btn_row.addWidget(backup_btn)
        self._backup_open_btn = QPushButton("📂  Open Location")
        self._backup_open_btn.setStyleSheet(
            f"background:{C['overlay']};color:{C['text']};font-weight:bold;"
            f"padding:8px 18px;border-radius:4px;border:none;")
        self._backup_open_btn.clicked.connect(self._open_backup_location)
        self._backup_open_btn.setVisible(False)
        backup_btn_row.addWidget(self._backup_open_btn)
        backup_btn_row.addStretch()
        sf2.addLayout(backup_btn_row)
        self._backup_status = QLabel("")
        self._backup_status.setWordWrap(True)
        self._backup_status.setStyleSheet(f"color:{C['subtext']};font-size:11px;")
        sf2.addWidget(self._backup_status)

        layout.addWidget(self._settings_frame)

        # Populate downloads field from current yaml value (if installed)
        if self._is_installed():
            self._load_dl_dir_from_yaml()

        # Progress
        prog_frame = QFrame()
        prog_frame.setStyleSheet(
            f"background:{C['surface']};border:1px solid {C['border']};border-radius:4px;")
        pf = QVBoxLayout(prog_frame)
        pf.setContentsMargins(14, 10, 14, 10)
        self._prog_lbl = QLabel("Ready.")
        self._prog_lbl.setStyleSheet(f"color:{C['subtext']};")
        pf.addWidget(self._prog_lbl)
        self._prog_bar = QProgressBar()
        self._prog_bar.setRange(0, 100)
        self._prog_bar.setValue(0)
        pf.addWidget(self._prog_bar)
        layout.addWidget(prog_frame)

        # Step list
        steps_frame = QFrame()
        steps_frame.setStyleSheet(
            f"background:{C['surface']};border:1px solid {C['border']};border-radius:4px;")
        sf = QVBoxLayout(steps_frame)
        sf.setContentsMargins(14, 12, 14, 12)
        QLabel("STEPS").setStyleSheet(f"color:{C['border']};font-size:9px;")
        hdr2 = QLabel("STEPS")
        hdr2.setStyleSheet(f"color:{C['border']};font-size:9px;font-weight:bold;")
        sf.addWidget(hdr2)
        self._step_labels = {}
        for key, desc in [
            ("git",   "Verify bundled EnvyCore"),
            ("tools", "Install media tools (FFmpeg, MKVToolNix, Bento4…)"),
            ("uv",    "Install uv package manager"),
            ("sync",  "uv lock & uv sync (Python packages)"),
            ("yaml",  "Copy example YAML config"),
            ("done",  "All done ✓"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel("○")
            lbl.setStyleSheet(f"color:{C['border']};font-size:14px;min-width:20px;")
            row.addWidget(lbl)
            row.addWidget(QLabel(desc))
            row.addStretch()
            sf.addLayout(row)
            self._step_labels[key] = lbl
        layout.addWidget(steps_frame)
        layout.addStretch()
        scroll.setWidget(page)
        outer_layout.addWidget(scroll)
        return outer

    def _start_build_exe(self):
        """Install PyInstaller if needed, generate a spec, and build the EXE."""
        import subprocess as _sp
        import sys as _sys
        import os as _os

        system_python = _sys.executable
        if getattr(_sys, "frozen", False):
            launcher_py = Path(_sys.executable).parent / "envy_launcher.py"
            if not launcher_py.exists():
                QMessageBox.warning(self, APP_NAME,
                    f"Could not find envy_launcher.py next to the exe:\n{launcher_py}\n\n"
                    "Place the source .py file in the same folder as the exe to rebuild.")
                return
        else:
            launcher_py = Path(_os.path.abspath(_sys.argv[0]))
        launcher_dir = launcher_py.parent
        assets_dir = launcher_dir / "assets"
        icon_path = assets_dir / "icon.ico"

        # Write a tiny launcher script — all it does is find system Python and
        # run envy_launcher.py with it, exactly like the batch file does.
        # This means the exe uses the system Python's installed packages
        # (PyQt6, winpty, etc.) so the download panel works identically to the batch file.
        launcher_stub = launcher_dir / "_envy_launcher_stub.py"
        launcher_stub.write_text(
            "import sys, os, subprocess, pathlib, ctypes\n"
            "\n"
            "# Claim the EnvyUI identity before spawning the app so Windows\n"
            "# associates the taskbar button with EnvyUI.exe, not pythonw.exe.\n"
            "try:\n"
            "    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TwinVine.EnvyUI')\n"
            "except Exception:\n"
            "    pass\n"
            "\n"
            "def _find_python():\n"
            "    import shutil\n"
            "    for name in ['pythonw.exe', 'python.exe']:\n"
            "        hit = shutil.which(name)\n"
            "        if hit and os.path.getsize(hit) > 0:\n"
            "            return hit\n"
            "    return None\n"
            "\n"
            "def _find_script():\n"
            "    exe_dir = pathlib.Path(sys.executable).parent\n"
            "    for folder in [exe_dir, exe_dir.parent]:\n"
            "        candidate = folder / 'envy_launcher.py'\n"
            "        if candidate.exists():\n"
            "            return candidate\n"
            f"    baked = pathlib.Path(r'{launcher_dir}')\n"
            "    candidate = baked / 'envy_launcher.py'\n"
            "    if candidate.exists():\n"
            "        return candidate\n"
            "    return None\n"
            "\n"
            "python = _find_python()\n"
            "if not python:\n"
            "    ctypes.windll.user32.MessageBoxW(\n"
            "        0,\n"
            "        'Python not found.\\n\\nPlease install Python from https://www.python.org/downloads/\\n'\n"
            "        'and tick \"Add Python to PATH\" during installation.',\n"
            "        'EnvyUI', 0x10)\n"
            "    sys.exit(1)\n"
            "\n"
            "script = _find_script()\n"
            "if not script:\n"
            "    ctypes.windll.user32.MessageBoxW(\n"
            "        0,\n"
            "        'envy_launcher.py not found next to EnvyUI.exe.',\n"
            "        'EnvyUI', 0x10)\n"
            "    sys.exit(1)\n"
            "\n"
            "os.chdir(str(script.parent))\n"
            "# Wait for the app to exit so EnvyUI.exe stays alive while the\n"
            "# app runs — Windows then pins EnvyUI.exe (not pythonw.exe).\n"
            "proc = subprocess.Popen([python, str(script)], cwd=str(script.parent))\n"
            "proc.wait()\n",
            encoding="utf-8"
        )

        build_args = [
            system_python, "-m", "PyInstaller",
            "--noconfirm", "--onefile", "--windowed", "--name", "EnvyUI",
        ]
        if icon_path.exists():
            build_args += ["--icon", str(icon_path)]
        build_args.append(str(launcher_stub))

        class _BuildWorker(QThread):
            log_line   = pyqtSignal(str)
            finished_ok = pyqtSignal(str)   # exe path
            finished_err = pyqtSignal(str)  # error message

            def __init__(self, python, launcher_dir, build_args):
                super().__init__()
                self._python = python
                self._cwd    = str(launcher_dir)
                self._args   = build_args

            def _stream(self, cmd):
                """Run cmd, stream stdout+stderr line by line, raise on non-zero exit."""
                cf = _sp.CREATE_NO_WINDOW
                proc = _sp.Popen(
                    cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, cwd=self._cwd, creationflags=cf)
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line:
                        self.log_line.emit(line)
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError(f"Process exited with code {proc.returncode}")

            def run(self):
                try:
                    self.log_line.emit("[build] Installing PyInstaller…")
                    self._stream([self._python, "-m", "pip", "install", "pyinstaller"])
                    self.log_line.emit("[build] Starting PyInstaller…")
                    self._stream(self._args)
                    import os as _os2, shutil as _sh
                    exe_built = _os2.path.join(self._cwd, "dist", "EnvyUI.exe")
                    if not _os2.path.exists(exe_built):
                        raise RuntimeError("Build finished but EnvyUI.exe not found in dist\\")
                    # Move exe to root folder (next to envy_launcher.py)
                    exe_dest = _os2.path.join(self._cwd, "EnvyUI.exe")
                    _sh.move(exe_built, exe_dest)
                    # Remove PyInstaller artefacts — not needed after build
                    for _item in ["dist", "build", "EnvyUI.spec", "_envy_launcher_stub.py"]:
                        _p = _os2.path.join(self._cwd, _item)
                        try:
                            if _os2.path.isdir(_p):
                                _sh.rmtree(_p)
                            elif _os2.path.isfile(_p):
                                _os2.remove(_p)
                        except Exception:
                            pass
                    # Create EnvyUI.lnk with AppUserModelID embedded so pinning
                    # the shortcut merges the running window into one taskbar button.
                    lnk_dest = _os2.path.join(self._cwd, "EnvyUI.lnk")
                    try:
                        import ctypes as _ct, struct as _st, uuid as _uu
                        _ole32   = _ct.windll.ole32
                        _shell32 = _ct.windll.shell32
                        _ole32.CoInitialize(None)

                        def _gb(s):
                            return (_ct.c_byte * 16)(*_uu.UUID(s).bytes_le)

                        # ── create IShellLinkW ──
                        _clsid = _gb("{00021401-0000-0000-C000-000000000046}")
                        _iid_sl = _gb("{000214F9-0000-0000-C000-000000000046}")
                        _pSL = _ct.c_void_p()
                        _ole32.CoCreateInstance(
                            _ct.byref(_clsid), None, 1,
                            _ct.byref(_iid_sl), _ct.byref(_pSL))
                        _vSL = _ct.cast(_pSL, _ct.POINTER(_ct.POINTER(_ct.c_void_p)))[0]
                        _WF = _ct.WINFUNCTYPE
                        _HR = _ct.HRESULT
                        _VP = _ct.c_void_p
                        _WS = _ct.c_wchar_p
                        _WF(_HR,_VP,_WS)(_vSL[20])(  _pSL, exe_dest)      # SetPath
                        _WF(_HR,_VP,_WS)(_vSL[9]) (  _pSL, self._cwd)     # SetWorkingDirectory
                        _WF(_HR,_VP,_WS)(_vSL[7]) (  _pSL, "EnvyUI")      # SetDescription
                        _WF(_HR,_VP,_WS,_ct.c_int)(_vSL[17])(_pSL, exe_dest, 0)  # SetIconLocation
                        # QI for IPersistFile and Save
                        _iid_pf = _gb("{0000010B-0000-0000-C000-000000000046}")
                        _pPF = _ct.c_void_p()
                        _WF(_HR,_VP,_VP,_ct.POINTER(_VP))(_vSL[0])(_pSL, _ct.byref(_iid_pf), _ct.byref(_pPF))
                        _vPF = _ct.cast(_pPF, _ct.POINTER(_ct.POINTER(_ct.c_void_p)))[0]
                        _WF(_HR,_VP,_WS,_ct.c_bool)(_vPF[6])(_pPF, lnk_dest, True)  # Save
                        _WF(_HR,_VP)(_vPF[2])(_pPF)   # Release IPersistFile
                        _WF(_HR,_VP)(_vSL[2])(_pSL)   # Release IShellLinkW

                        # ── set AppUserModelID on the saved .lnk ──
                        _iid_ps = _gb("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
                        _pPS = _ct.c_void_p()
                        _shell32.SHGetPropertyStoreFromParsingName.restype  = _ct.HRESULT
                        _shell32.SHGetPropertyStoreFromParsingName.argtypes = [
                            _ct.c_wchar_p, _ct.c_void_p, _ct.c_uint,
                            _ct.c_void_p, _ct.POINTER(_ct.c_void_p)]
                        _shell32.SHGetPropertyStoreFromParsingName(
                            lnk_dest, None, 2,
                            _ct.byref(_iid_ps), _ct.byref(_pPS))
                        if _pPS:
                            _vPS = _ct.cast(_pPS, _ct.POINTER(_ct.POINTER(_ct.c_void_p)))[0]
                            # PROPERTYKEY: {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, pid=5
                            _pk = (_ct.c_byte*20)(
                                *_uu.UUID("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3").bytes_le
                                + _st.pack("<I", 5))
                            _aumid_buf = _ct.create_unicode_buffer("TwinVine.EnvyUI")
                            # PROPVARIANT: vt=VT_LPWSTR(31), 6 pad bytes, pointer
                            _pv = (_ct.c_byte*16)(
                                *_st.pack("<H6sQ", 31, b'\x00'*6,
                                          _ct.addressof(_aumid_buf)))
                            _WF(_HR,_VP,_VP,_VP)(_vPS[6])(_pPS, _ct.byref(_pk), _ct.byref(_pv))
                            _WF(_HR,_VP)(_vPS[7])(_pPS)   # Commit
                            _WF(_HR,_VP)(_vPS[2])(_pPS)   # Release
                    except Exception:
                        pass
                    self.finished_ok.emit(exe_dest)
                except Exception as e:
                    self.finished_err.emit(str(e))

        self._build_worker = _BuildWorker(system_python, launcher_dir, build_args)
        self._build_worker.log_line.connect(self._append_log)
        self._build_worker.finished_ok.connect(lambda exe: (
            self._build_status.setStyleSheet(f"color:{C['green']};font-size:11px;border:none;"),
            self._build_status.setText(
                f"✓  Done — EnvyUI.exe + EnvyUI.lnk saved to: {Path(exe).parent}  "
                "│  To pin: unpin the old exe, then right-click EnvyUI.lnk → Pin to taskbar"),
            self._build_btn.setEnabled(True),
        ))
        self._build_worker.finished_err.connect(lambda msg: (
            self._build_status.setStyleSheet(f"color:{C['red']};font-size:11px;border:none;"),
            self._build_status.setText(f"✗  {msg}"),
            self._build_btn.setEnabled(True),
        ))
        self._build_btn.setEnabled(False)
        self._build_status.setStyleSheet(f"color:{C['yellow']};font-size:11px;border:none;")
        self._build_status.setText("⏳  Building…  (see Log page for output)")
        self._build_worker.start()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose install directory",
                                             self._dir_entry.text())
        if d:
            self._dir_entry.setText(d)

    def _set_step(self, key, state):
        icons  = {"pending": "○", "active": "◉", "done": "✓", "error": "✗"}
        colors = {"pending": C['border'], "active": C['yellow'],
                  "done": C['green'], "error": C['red']}
        lbl = self._step_labels.get(key)
        if lbl:
            lbl.setText(icons.get(state, "○"))
            lbl.setStyleSheet(f"color:{colors.get(state, C['border'])};font-size:14px;min-width:20px;")

    def _start_install(self):
        # Confirmation dialog explaining what will happen
        msg = QMessageBox(self)
        msg.setWindowTitle("Install EnvyUI Tools")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("<b>This will set up EnvyUI on your machine.</b>")
        msg.setInformativeText(
            "The following will happen:\n\n"
            "\u2022 Download and install media tools (~500MB total)\n"
            "\u2022 Create a Python virtual environment\n"
            "\u2022 Patch configuration files (backups are kept)\n\n"
            "Note: some tools are installed outside the EnvyCore folder:\n"
            "\u2022 uv \u2192 your user profile (~/.local/bin)\n"
            "\u2022 FFmpeg, MKVToolNix, N_m3u8DL-RE, Bento4 \u2192 C:\\Tools\\bin\n"
            "\u2022 Git for Windows \u2192 system-wide (if not already installed)\n\n"
            "See the Help page for full uninstall details.\n\n"
            "Do you want to continue?"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        # ── Backup warning — shown only if a previous install is detected ──
        _candidate_dir = Path(self._dir_entry.text())
        _existing_yaml = _candidate_dir / "packages" / "envied" / "src" / "envied" / "envied.yaml"
        if _existing_yaml.exists():
            _bak_msg = QMessageBox(self)
            _bak_msg.setWindowTitle("Back Up Your Settings First")
            _bak_msg.setIcon(QMessageBox.Icon.Warning)
            _bak_msg.setText("<b>A previous installation was detected.</b>")
            _bak_msg.setInformativeText(
                "Before continuing, we strongly recommend backing up:\n\n"
                "• Your credentials and settings — go to the Settings tab "
                "and click Backup Settings to save a timestamped copy of envied.yaml\n"
                "• Any cookie files you have in EnvyCore\\Cookies\\\n\n"
                "The virtual environment will be rebuilt during this install. "
                "Your envied.yaml will not be overwritten but maybe patched, but it is always safer "
                "to have a backup before proceeding.\n\n"
                "Have you backed up your settings?"
            )
            _bak_msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            _bak_msg.button(QMessageBox.StandardButton.Yes).setText("Yes, continue")
            _bak_msg.button(QMessageBox.StandardButton.Cancel).setText("Cancel — I'll back up first")
            _bak_msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if _bak_msg.exec() != QMessageBox.StandardButton.Yes:
                return

        self.install_dir = Path(self._dir_entry.text())
        self.cfg["install_dir"] = str(self.install_dir)
        save_config(self.cfg)
        for k in self._step_labels:
            self._set_step(k, "pending")
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing…")
        self._prog_bar.setValue(0)
        # Stay on install page — progress bar and step indicators show progress

        self._install_worker = InstallWorker(self.install_dir)
        self._install_worker.log_line.connect(self._append_log)
        self._install_worker.step_done.connect(self._set_step)
        self._install_worker.progress.connect(self._update_install_progress)
        self._install_worker.finished.connect(self._on_install_done)
        self._install_worker.start()

    def _venv_python(self) -> str | None:
        """Return path to the venv Python executable if it exists."""
        # Check TWINVINE_VENV env var first (set by .bat on launch)
        venv_from_env = os.environ.get("TWINVINE_VENV", "")
        candidates = []
        if venv_from_env:
            candidates.append(Path(venv_from_env))
        candidates.append(self.install_dir / ".venv")
        for venv_root in candidates:
            for name in ("pythonw.exe", "python.exe"):
                p = venv_root / "Scripts" / name
                if p.exists():
                    return str(p)
        return None

    def _do_relaunch(self):
        """Relaunch via the .bat file — uses system pythonw for clean windowless start."""
        launcher_dir = Path(__file__).resolve().parent
        bat = launcher_dir / "TwinVine Launcher.bat"
        launcher_script = str(Path(__file__).resolve())

        # Set TWINVINE_VENV so the relaunched process knows where the venv is
        venv_py = self._venv_python()
        env = os.environ.copy()
        if venv_py:
            env["TWINVINE_VENV"] = str(Path(venv_py).parent.parent)

        # Use system pythonw.exe — always properly windowless unlike venv copies
        sys_pythonw = shutil.which("pythonw")
        if sys_pythonw:
            subprocess.Popen(
                [sys_pythonw, launcher_script],
                cwd=str(launcher_dir),
                env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            QApplication.quit()
            return

        # Fallback: use the bat
        if bat.exists():
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat)],
                cwd=str(launcher_dir),
                env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            QApplication.quit()
            return

        QMessageBox.warning(self, APP_NAME,
            "Restart complete. Please close and reopen the launcher manually.")

    def _update_install_progress(self, v: float, m: str):
        pct = int(v * 100)
        self._prog_bar.setValue(pct)
        self._prog_lbl.setText(m)
        if pct >= 50:
            self._prog_bar.setStyleSheet(
                f"QProgressBar{{background:{C['surface']};border:1px solid {C['border']};"
                f"border-radius:3px;color:{C['bg']};font-size:11px;font-weight:bold;}}"
                f"QProgressBar::chunk{{background:{C['green']};border-radius:3px;}}"
            )
        else:
            self._prog_bar.setStyleSheet(
                f"QProgressBar{{background:{C['surface']};border:1px solid {C['border']};"
                f"border-radius:3px;color:{C['text']};font-size:11px;}}"
                f"QProgressBar::chunk{{background:{C['green']};border-radius:3px;}}"
            )

    def _on_install_done(self, success: bool, msg: str):
        self._install_btn.setEnabled(True)
        self._install_btn.setText("▶  Install EnvyUI Tools")
        if success:
            self.cfg.update({
                "installed": True,
                "install_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "uv_exe": getattr(self._install_worker, "uv_exe_path", None),
            })
            save_config(self.cfg)
            self._refresh_status()
            self._update_btn.setVisible(True)
            if hasattr(self, '_build_frame'):
                self._build_frame.setVisible(True)
            if hasattr(self, '_settings_frame'):
                self._settings_frame.setVisible(True)
                self._load_dl_dir_from_yaml()

            self._populate_service_buttons()
            self._append_log("=" * 60)
            self._append_log("INSTALLATION COMPLETE — service buttons are available.")
            self._append_log("=" * 60)
            self._prog_lbl.setText("✓ Complete! Go to Home tab.")
            self._prog_lbl.setStyleSheet(f"color:{C['green']};font-weight:bold;")
            if hasattr(self, "_mini_log"):
                self._mini_log.appendPlainText("✓ Done — check Log tab for any warnings.")
        else:
            # Show failure in log (don't hide it with a popup)
            self._append_log("=" * 60)
            self._append_log(f"INSTALLATION FAILED: {msg}")
            self._append_log("=" * 60)
            QMessageBox.critical(self, APP_NAME, f"Installation failed:\n{msg}")

    def _check_updates(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Checking…")
        # Use a QThread (not threading.Thread) so QTimer.singleShot works correctly
        self._update_thread = _UpdateCheckThread(APP_VERSION)
        self._update_thread.result_ready.connect(self._on_update_result)
        self._update_thread.start()

    def _on_update_result(self, remote: str, local: str):
        """Called on main thread when update check completes."""
        self._update_btn.setEnabled(True)
        self._update_btn.setText("🔄  Check for Updates")
        if not remote:
            QMessageBox.warning(self, APP_NAME,
                "Could not reach GitHub. Check your internet connection.")
            return
        if remote == local:
            QMessageBox.information(self, APP_NAME, "✓ EnvyUI is up to date!")
        else:
            ans = QMessageBox.question(self, APP_NAME,
                f"EnvyUI v{remote} is available (you have v{local}).\n\n"
                "To update:\n"
                "1. Click 'Back Up Settings' on the Install page (just to be safe)\n"
                "2. Close the app\n"
                "3. Download the new zip and extract it over your existing EnvyUI folder\n"
                "   — your envied.yaml, cookies, and CDM files are NOT in the zip so they\n"
                "   will not be overwritten\n"
                "4. Reopen the app and click 'Install EnvyUI Tools' to update the environment\n\n"
                "Open the GitHub releases page now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                import webbrowser as _wb
                _wb.open(f"https://github.com/{GITHUB_REPO}/releases")

    # ── Log page ─────────────────────────────────────────────────────────────

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        hdr_row = QHBoxLayout()
        hdr = QLabel("Log")
        hdr.setStyleSheet(f"font-size:20px;font-weight:bold;color:{C['green']};")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_log)
        hdr_row.addWidget(clear_btn)
        back_btn = QPushButton("← Back to Home")
        back_btn.clicked.connect(lambda: self._show_page("download"))
        hdr_row.addWidget(back_btn)
        layout.addLayout(hdr_row)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet(
            f"background:#070910;color:#a6e3a1;font-family:Consolas,monospace;"
            f"font-size:10px;border:1px solid {C['border']};")
        layout.addWidget(self._log_view)
        return page

    def _append_log(self, msg: str):
        # Mirror key lines to mini install log
        if hasattr(self, "_mini_log"):
            if any(x in msg for x in ["── STEP", "INSTALL", "ERROR", "WARNING",
                                        "Downloading", "Installed", "skipped",
                                        "already", "FFmpeg", "uv sync", "Done"]):
                self._mini_log.appendPlainText(msg)
                sb = self._mini_log.verticalScrollBar()
                sb.setValue(sb.maximum())
        ts = datetime.now().strftime("%H:%M:%S")
        # Must be called on main thread for Qt safety — use invokeMethod pattern
        def _do():
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)
            self._log_view.insertPlainText(f"[{ts}] {msg}\n")
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)
        if QApplication.instance() and threading.current_thread() is threading.main_thread():
            _do()
        else:
            QTimer.singleShot(0, _do)

    def _clear_log(self):
        self._log_view.clear()

    # ── About page ────────────────────────────────────────────────────────────

    def _build_help_page(self) -> QWidget:
        """
        Help page — edit the HELP_TEXT constant below to update the content.
        Supports basic Markdown-style sections using ## for headers.
        """
        # ── EDIT THIS TEXT TO UPDATE THE HELP PAGE ────────────────────────────
        HELP_TEXT = """
## Before You Install

**When you run EnvyUI.bat for the first time** the following packages are installed automatically into your system Python before the app opens:

- **PyQt6** — the UI framework EnvyUI is built on
- **PyQt6-WebEngine** — powers the in-app terminal panel
- **pywinpty** — enables real-time terminal output during downloads
- **certifi** — SSL certificate bundle for secure connections
- **uv** — the Python package manager used to build the EnvyCore environment

These are installed into your system Python via pip and only happen if each package is not already present. You will see a brief console message for each one being installed.

**After the app opens**, click **Install EnvyUI Tools** on the Install / Update page to complete the setup:

- A **Python virtual environment** is created inside EnvyCore and all required packages are installed (~150MB)
- The following tools are downloaded and installed to `C:\\Tools\\bin`: **FFmpeg** (~240MB), **N_m3u8DL-RE**, **Shaka Packager**, **mp4decrypt** (Bento4), **dovi_tool**, **hdr10plus_tool**, **CCExtractor**
- **MKVToolNix** is installed via winget and its executables are copied to `C:\\Tools\\bin`
- **SubtitleEdit** is installed to `C:\\Tools\\SubtitleEdit`
- **Git for Windows** is installed system-wide via winget if not already present
- Configuration files are patched where needed — backups of any modified files are kept alongside the originals

**Total download for Install EnvyUI Tools:** approximately 500MB. **Time:** 2–5 minutes on a fast connection.

If you delete the EnvyCore folder and reinstall, tools already in `C:\\Tools\\bin` are detected and skipped — only the EnvyCore Python environment will be rebuilt. To fully uninstall everything you would also need to delete `C:\\Tools\\bin`, `C:\\Tools\\SubtitleEdit`, and remove uv and Git manually.

---

## Getting Started

1. On first run, click **Install / Update** in the sidebar and then **Install EnvyUI Tools**. Check the **Log** tab for a detailed view of what is being installed.
2. Once installed, return to **Home** and click a service button (BBC, ITVX, etc.), then search by keyword or paste a URL.
3. Select the series and episodes you want, then click **Confirm**.
4. Before downloading you can set a few options — see **Options** below for details.

---

## Navigation

- **Home** — The main page. Click a service button to start, type keywords to search, or paste a direct episode URL into the search box.
- **Extended Services** — Additional streaming platforms accessed by URL, with a Browse Series episode picker.
- **My Downloads** — Opens your downloads folder in Windows Explorer.
- **Install / Update** — Install or update EnvyUI and all media tools. After the first install this page also provides: Back Up Settings, Build EXE, and Change Download Location.
- **Log** — Detailed output from the launcher, useful for diagnosing issues.
- **Help** — You are here.
- **About** — Information about EnvyUI, TwinVine, and their authors.

---

## Options

- **Envied Config** — Opens envied.yaml in Notepad. Edit credentials, download location, filename format, subtitle settings and more.
- **HLG** — Enables HDR/HLG streams when ticked (on by default). If a download fails with "Selection unavailable in UHD" or "Stream not available in that resolution", the app will automatically retry in SDR. If the retry also fails, untick HLG and try again manually.
- **Quality** — Choose from Best available, 2160p, 1080p, or 720p. Best available will fall back to the highest resolution found if your chosen resolution is not available. For resolutions lower than 720p use Fetch Tracks in the URL Download panel.
- **No subtitles** — Skip subtitle downloads for all selected episodes.
- **Slow mode** — Adds a randomised delay between episode downloads. Set your preferred minimum and maximum wait time in seconds once the box is ticked.
- **Fetch Tracks** — Available in the Download by URL panel. Paste an episode or series URL, click **Fetch Tracks**, and a dropdown list of all available resolutions will appear. Select your preferred resolution and click Download. Note that 2160p may not always appear in the list — if so, try Best available or use the standard Quality options instead.

---

## Downloading Episodes

When you click a service button you can choose from four actions:

- **Search by keyword** — Type a show name to find it.
- **Greedy Search by URL** — Paste a show page URL to fetch all available content.
- **Download by URL** — Paste a direct episode or series URL to download it.
- **Browse by Category** — Browse the service's categories to find content.

After searching, select the series you want, tick the episodes and click **Confirm**. Multiple episodes download sequentially — you can mix episodes from different series. Progress is shown in the download panel with a live log and a cancel option.

**2160p Downloads from BBC iPlayer** — 2160p content is not always returned by Best available or shown in Fetch Tracks. For reliable 2160p downloads, use the full programme title exactly as listed on the BBC website. See: <a href="https://www.bbc.co.uk/iplayer/help/questions/programme-availability/uhd-content">What programmes can I watch in Ultra HD?</a> — or select 2160p explicitly as your Quality choice.

---

## Extended Services

The **Extended Services** page supports additional platforms not shown on the main Home page: NRK, ThreeNow, PBS, VM (Virgin Media Television), Rakuten and more.

**Downloading by URL** — Paste a direct episode or series URL into the URL field and click **Download**. For series pages where you want to pick specific episodes, use the **Browse Series** button instead.

**Selecting specific episodes without Browse Series** — If you have a series URL but cannot easily get individual episode URLs from the website, you can append an episode identifier to the URL field with a space followed by `-w` and the episode code, for example: `-w s01e01` or `-w s01e01-s01e03` for a range.

**Fetch Tracks** — Click this before downloading to see all available resolutions for the URL you have entered. Select your preferred resolution from the dropdown that appears, then click Download.

---

## Batch Mode

Batch mode lets you queue episodes from multiple shows before downloading them all at once.

1. Toggle **Batch Mode** on in the sidebar — Batch Mode text turns green when active.
2. Search and select episodes as normal — they queue instead of downloading immediately. The sidebar shows how many episodes are queued.
3. When ready, click **Run Batch** to download everything in the queue.
4. If you need to start your batch list over, click the **Clear** button to empty the queue.
5. Batch Mode will return to normal automatically once the batch completes.

---

## Build EXE

The **Build EXE** button on the Install / Update page creates `EnvyUI.exe` — a small launcher that finds your existing Python installation and runs `envy_launcher.py` with it. Because it uses the same Python and packages as the batch file, downloads behave identically.

Both `EnvyUI.exe` and `EnvyUI.lnk` are saved to the same folder as `envy_launcher.py`. Building takes a minute or two — the Log tab shows progress.

**How to use the output:**

- **Launch / desktop shortcut** — double-click `EnvyUI.exe`, or right-click it in File Explorer → *Pin to Start*.
- **Taskbar pin** — right-click `EnvyUI.lnk` → *Pin to taskbar*. You must use the shortcut file for this; pinning the exe directly causes two icons to appear when the app is running.
- **Updates** — most updates only require replacing `envy_launcher.py`. The exe and shortcut do not need to be rebuilt unless specifically noted in the release notes.

---

## Metadata Tagging (IMDB / TMDB / TVDB)

After each download, EnvyUI attempts to look up the title and embed metadata tags (IMDB ID, TMDB ID, TVDB ID) directly into the MKV file. Media servers such as Plex, Jellyfin, and Infuse use these tags to instantly match the file to the correct show or movie without guessing from the filename.

**IMDBApi error messages during downloads**

You may occasionally see retry errors like `Retrying ... IMDBApi` in the download log. This means the IMDBApi service is temporarily unreachable. EnvyUI will automatically skip it and fall through to TMDB or OMDb — your download will complete normally. No action is required, but adding a TMDB or OMDb API key (below) ensures metadata is always found even when IMDBApi is unavailable.

**Adding a TMDB API key (recommended)**

TMDB is the most comprehensive metadata source and is free to use.

1. Create a free account at <a href="https://www.themoviedb.org">themoviedb.org</a>
2. Go to **Settings → API** and request an API key (choose Developer)
3. Copy your **API Read Access Token** (the long one) — or the shorter **API Key (v3)**
4. Open envied.yaml inside **EnvyCore / packages / envied / src / envied /**
5. Find the line tmdb_api_key: "" and add your key between the quotes

**Adding an OMDb API key (optional fallback)**

OMDb is a secondary fallback backed by IMDb data. The free tier allows 1,000 requests per day.

1. Register for a free key at <a href="https://www.omdbapi.com/apikey.aspx">omdbapi.com/apikey.aspx</a>
2. Activate the key from the confirmation email
3. Open envied.yaml and find the line omdb_api_key: "" and add your key between the quotes
4. If you're uprgading the app and don't have the line omdb_api_key: "" in your envied yaml file then you will need to add it just below tmdb_api_key: "" line .

**Provider priority order**

EnvyUI tries providers in this order, skipping any that are unavailable:

1. **IMDBApi** — free, no key needed, but may be down occasionally
2. **TMDB** — requires a free API key, most reliable
3. **OMDb** — requires a free API key, good fallback
4. **SIMKL** — requires a free client ID, niche/anime focus (If you don't have SIMKL in your envied yaml file just add this line simkl_client_id: "" below omdb_api_key: "" 

The coloured dots in the sidebar show which providers are currently active (green = available, red = unavailable or no key).

---

## Download Folder Structure

EnvyUI automatically organises downloads into a folder structure compatible with Plex, Jellyfin, Kodi, and other media servers.

**TV Series**

```
Downloads/
  Death in Paradise (2011)/
    Season 01/
      Death in Paradise S01E01 Episode Name.mkv
    Season 02/
      Death in Paradise S02E01 Episode Name.mkv
```

The show folder always uses the **year the series first aired**, not the year of the individual episode. This means all seasons of a show land in the same top-level folder regardless of which season you download first.

**Movies**

```
Downloads/
  The Matrix (1999)/
    The Matrix (1999).mkv
```

**How the year is determined**

The premiere year is looked up automatically from the metadata providers (IMDBApi, TMDB, or OMDb) when a download starts. If no metadata match is found, the year shown by the service for that episode is used as a fallback. Adding a TMDB or OMDb API key (see Metadata Tagging above) ensures the correct year is found reliably.

**Changing the folder and filename format**

The templates that control folder names and filenames are configured in envied.yaml under `output_template`. Click **Envied Config** on the Home page to open envied.yaml. The available template variables and example formats are documented in the comments inside the file.

---

## Supported Services

**Please note: Some services require login credentials and/or cookie files.** To add credentials, click **Envied Config** on the Home page and fill in the relevant section for each service. To add cookies, place your cookie text file in the **Cookies** folder inside EnvyCore — the file should be named after the service, for example: `PBS.txt`.

**⚠ While some services on the app may have paid for or subscription plans we can only offer support or bug reports for Free-to-air content only as we are unable to test anything other than services that offer Free-to-air content and while envied lists over 60 services we only list services that we have been able to test and are known to work with the app, you can of course still use envied from a terminal window inside the EnvyCore folder if you're familiar with envied commands structure. All services listed below are free to watch without a subscription (though some require a free account for login).**

**Main page services:** ALL4, BBC iPlayer, ITVX, My5, U (UKTV), RTE, STV, TPTV, Rakuten TV, Tubi, Pluto TV, VM Play (IE), TVNZ, ThreeNow (NZ), ABC iView (AU), 7plus (AU), 9Now (AU), 10play (AU), SBS On Demand (AU), Roku (US), CBS (US), NBC, PBS, The CW (US), Crave, CBC Gem, Plex

**Extended Services page:** **Extended Services page:** Blaze TV, NFBC, RTE+, NPO, ARD Mediathek, NRK

**Reordering or hiding main page buttons** — The order of the main page service buttons can be customised by editing the `CORE_SERVICES` list near the top of `envy_launcher.py`. Each entry is a short line like `{"id": "ALL4", "label": "ALL4"}` — simply cut and paste lines into whichever order you prefer and restart the app. If more than 14 services are on the main page, buttons are spread across pages navigated with the Prev / Next controls that appear in the Services header. To hide a service you don't use, add a `#` at the start of its line to comment it out — it will no longer appear on the main page but can be restored at any time by removing the `#`.

**Adjusting the app height for smaller screens** — If the app is too tall for your screen, open `envy_launcher.py` in a text editor and find `self.resize(1100, 900)` near the top of the file. Change the `900` to a smaller value — `750` works well on most 15.6" laptops. The download panel will automatically adjust to fit.

---

## Common Errors

**"Selection unavailable in UHD"** or **"Stream not available in that resolution"** — The app will automatically retry in SDR. If that also fails, untick the HLG checkbox and try again.

**"No .venv found"** — Go to Install / Update and click Install EnvyUI Tools.

**"Download fails with exit code 1"** — Check your credentials in Envied Config and make sure your CDM device file is in the WVDs folder.

**"unable to find vault command"** — Run Install / Update again to repair the configuration.

**NVIDIA GeForce overlay popup on launch** — The terminal panel inside EnvyUI uses a GPU-accelerated Chromium surface which NVIDIA GeForce Experience detects as a game. To fix this, open GeForce Experience, go to Settings → In-Game Overlay → click the gear icon → find EnvyUI in the application list and exclude it.

---

## Tips

- Downloads are saved to the **EnvyCore/Downloads** folder by default, organised into show/movie subfolders automatically. You can change the download location on the Install / Update page under Change Download Location.
- The **Log** tab records everything — check it first if something goes wrong.
- Login credentials for each service go in **Envied Config** under the credentials section.
- Cookie files go in the **Cookies** folder inside EnvyCore, named after the service (e.g. `PBS.txt`).
- Use **Back Up Settings** on the Install / Update page before updating or reinstalling — it saves your envied.yaml, WVDs, PRDs, and Cookies to a timestamped folder on your Desktop.
- Some services are region-locked and may require a VPN depending on your location. If a download fails with an access or geo-restriction error, try connecting via a VPN for that service's country.
- If a fresh install fails, delete the EnvyCore subfolder and try again.

---

## Updating the App

EnvyUI updates are distributed as zip files on the GitHub releases page. When a new version is available the built-in update checker will open the releases page in your browser so you can download the latest zip.

**To update:**

1. Click **Back Up Settings** on the Install / Update page — this creates a timestamped folder on your Desktop containing your envied.yaml, WVDs, PRDs, and Cookies.
2. Close the app.
3. Download the new zip from the GitHub releases page and extract it over your existing EnvyUI folder. Your envied.yaml, cookies, and CDM files are not included in the zip so they will not be overwritten.
4. Reopen the app and click **Install EnvyUI Tools** to update the Python environment.
5. If you are using `EnvyUI.exe`, most updates only require replacing `envy_launcher.py` — no need to rebuild the exe unless the release notes say otherwise.

        """
        # ─────────────────────────────────────────────────────────────────────

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)

        hdr = QLabel("Help")
        hdr.setStyleSheet(f"font-size:20px;font-weight:bold;color:{C['green']};")
        layout.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 8, 16, 8)
        content_layout.setSpacing(4)

        for raw_line in HELP_TEXT.strip().split('\n'):
            if raw_line.startswith('## '):
                lbl = QLabel(raw_line[3:])
                lbl.setStyleSheet('color:#a6e3a1;font-size:14px;font-weight:bold;padding-top:12px;')
                content_layout.addWidget(lbl)
            elif not raw_line.strip():
                sp = QLabel('')
                sp.setFixedHeight(4)
                content_layout.addWidget(sp)
            else:
                import re as _re2
                rich = _re2.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', raw_line)
                lbl = QLabel(rich)
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setWordWrap(True)
                lbl.setOpenExternalLinks(True)
                lbl.setStyleSheet('color:#a6adc8;font-size:12px;')
                content_layout.addWidget(lbl)
        content_layout.addStretch()
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)
        return page




    def _build_about_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        # ── Section 1: EnvyUI ─────────────────────────────────────────────────
        lnch_title = QLabel("EnvyUI")
        lnch_title.setStyleSheet(
            f"font-size:20px;font-weight:bold;color:{C['green']};padding-bottom:4px;")
        layout.addWidget(lnch_title)

        lnch_ver = QLabel(f"Version {APP_VERSION}")
        lnch_ver.setStyleSheet(
            f"color:{C['subtext']};font-size:12px;padding-bottom:12px;")
        layout.addWidget(lnch_ver)

        lnch_info = QLabel(
            "EnvyUI is a Windows GUI application built on top of the TwinVine/Envied "
            "ecosystem. It calls Envied directly — no VineFeeder dependency — giving "
            "it a smaller footprint and faster setup than the original launcher.\n\n"
            "The one-click installer handles everything automatically: Git, FFmpeg, "
            "MKVToolNix, Bento4, Shaka Packager and all other required tools, plus "
            "the full Python virtual environment. Once installed, click a service "
            "button, search for a show, pick your episodes, and download.\n\n"
            "In addition to the core services available on the main page, EnvyUI "
            "includes an Extended Services page supporting a growing range of "
            "additional platforms across the US, UK, Australia, New Zealand, "
            "Canada, and Europe — accessible via direct URL or the Browse Series "
            "episode picker.\n\n"
            "Other features include batch mode for queuing multiple downloads, slow "
            "mode for rate-limited services, Fetch Tracks for inspecting available "
            "resolutions, and a built-in update checker. "
            "Everything runs in one clean dark-themed window.\n\n"
            "When a new version is available the update checker will direct you to "
            "the GitHub releases page to download the latest zip. Extract it over "
            "your existing install and run Install EnvyUI Tools to apply the update — "
            "your credentials and settings are preserved automatically."
        )
        lnch_info.setWordWrap(True)
        lnch_info.setStyleSheet(
            f"color:{C['text']};font-size:12px;line-height:1.6;padding-bottom:12px;")
        layout.addWidget(lnch_info)

        lnch_btn = QPushButton("EnvyUI on GitHub")
        lnch_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};padding:8px 20px;"
            f"border-radius:4px;font-weight:bold;border:none;")
        lnch_btn.clicked.connect(lambda: webbrowser.open(LAUNCHER_URL))
        lnch_btn.setFixedWidth(200)
        layout.addWidget(lnch_btn)

        # ── Divider ───────────────────────────────────────────────────────────
        layout.addSpacing(30)
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color:{C['border']};margin:0;")
        layout.addWidget(div)
        layout.addSpacing(30)

        # ── Section 2: TwinVine original project ─────────────────────────────
        tv_title = QLabel("TwinVine")
        tv_title.setStyleSheet(
            f"font-size:18px;font-weight:bold;color:{C['green']};padding-bottom:4px;")
        layout.addWidget(tv_title)

        tv_sub = QLabel("VineFeeder + Envied")
        tv_sub.setStyleSheet(f"color:{C['subtext']};font-size:12px;padding-bottom:12px;")
        layout.addWidget(tv_sub)

        tv_info = QLabel(
            "TwinVine is an open-source project created by vinefeeder / A_n_g_e_l_a.\n\n"
            "It combines VineFeeder (a service scraper and download manager) with Envied "
            "(a DRM decryption and media processing engine) to download content from a "
            "range of streaming services including BBC iPlayer, ITVX, All4, My5, STV, "
            "RTE, TPTV, TVNZ, Plex and more.\n\n"
            "Full credit for the underlying technology goes to the original authors. "
            "Without their work EnvyUI would not exist."
        )
        tv_info.setWordWrap(True)
        tv_info.setStyleSheet(
            f"color:{C['text']};font-size:12px;line-height:1.6;padding-bottom:12px;")
        layout.addWidget(tv_info)

        tv_btn = QPushButton("TwinVine on GitHub")
        tv_btn.setStyleSheet(
            f"background:{C['green']};color:{C['bg']};padding:8px 20px;"
            f"border-radius:4px;font-weight:bold;border:none;")
        tv_btn.clicked.connect(lambda: webbrowser.open("https://github.com/vinefeeder/TwinVine"))
        tv_btn.setFixedWidth(200)
        layout.addWidget(tv_btn)

        layout.addStretch()
        scroll.setWidget(inner)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        return outer

    # ── Status helpers ────────────────────────────────────────────────────────

    def _is_installed(self) -> bool:
        # Require the venv to exist — TwinVine folder alone isn't enough
        # (it's present immediately after zip extraction, before uv sync runs)
        return (bool(self.cfg.get("installed"))
                and (self.install_dir / ".venv").exists())

    def _refresh_status(self):
        installed = self._is_installed()
        if installed:
            self._status_badge.setText("● Installed")
            self._status_badge.setStyleSheet(
                f"color:{C['green']};font-size:9px;padding:8px;")
            # Show all steps as done when already installed
            if hasattr(self, "_step_labels"):
                for k in self._step_labels:
                    self._set_step(k, "done")
            if hasattr(self, "_prog_bar"):
                self._prog_bar.setValue(100)
            if hasattr(self, "_prog_lbl"):
                self._prog_lbl.setText("✓ EnvyUI Tools are installed.")
                self._prog_lbl.setStyleSheet(f"color:{C['green']};")
        else:
            self._status_badge.setText("● Not installed")
            self._status_badge.setStyleSheet(
                f"color:{C['red']};font-size:9px;padding:8px;")
            if hasattr(self, "_step_labels"):
                for k in self._step_labels:
                    self._set_step(k, "pending")
            if hasattr(self, "_prog_bar"):
                self._prog_bar.setValue(0)
            if hasattr(self, "_prog_lbl"):
                self._prog_lbl.setText("Ready.")
                self._prog_lbl.setStyleSheet(f"color:{C['subtext']};")


    def _check_metadata_providers(self):
        """Run provider availability checks in a background thread and update sidebar dots."""
        self._provider_check_thread = _ProviderCheckThread()
        self._provider_check_thread.result_ready.connect(self._on_provider_check_result)
        self._provider_check_thread.start()

    def _on_provider_check_result(self, payload: str):
        if not payload:
            return
        for part in payload.split(","):
            try:
                name, val = part.split(":")
                ok = val == "1"
            except ValueError:
                continue
            dot = self._meta_status_labels.get(name)
            if dot:
                colour = C["green"] if ok else C["red"]
                dot.setStyleSheet(f"color:{colour};font-size:9px;border:none;")
                dot.setToolTip(f"{name}: {'available' if ok else 'unavailable / no key'}")


# ── Entry ─────────────────────────────────────────────────────────────────────

def main():
    # High-DPI support
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = EnvyLauncher()
    window.show()
    QTimer.singleShot(500, window._check_metadata_providers)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
