#!/usr/bin/env python3
"""
VideoShrink  —  drag-and-drop video compressor
Requires: PySide6, ffmpeg in PATH
Install:  pip install PySide6
"""

import os
import sys
import json
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import (Qt, Signal, QObject, QMimeData, QThread)
from PySide6.QtGui  import QDragEnterEvent, QDropEvent, QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QComboBox, QLineEdit, QScrollArea,
    QFrame, QProgressBar, QFileDialog, QMessageBox, QSizePolicy,
    QStackedWidget,
)

# ─────────────────────────── constants ───────────────────────────────────────

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts"}

CODECS = {
    "H.265 / HEVC  —  best compression":    "libx265",
    "H.264 / AVC   —  best compatibility":  "libx264",
}
PRESETS = ["ultrafast", "superfast", "veryfast", "faster",
           "fast", "medium", "slow", "slower", "veryslow"]

# ─────────────────────────── stylesheet ──────────────────────────────────────

QSS = """
QWidget {
    background: #12141a;
    color: #dde3f0;
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
    font-size: 13px;
}

/* ── sidebar ── */
#sidebar {
    background: #1a1d27;
    border-left: 1px solid #2a2e3f;
}
#sidebar QLabel#section {
    color: #4a5170;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}

/* ── header ── */
#header {
    background: #1a1d27;
    border-bottom: 1px solid #2a2e3f;
}

/* ── buttons ── */
QPushButton {
    background: #252836;
    color: #8892aa;
    border: 1px solid #2a2e3f;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover  { background: #2d3245; color: #dde3f0; }
QPushButton:pressed { background: #222533; }
QPushButton:disabled { color: #3a3f55; border-color: #222533; }

QPushButton#primary {
    background: #3b6ef5;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 0;
    font-size: 14px;
    font-weight: 700;
}
QPushButton#primary:hover   { background: #4a7dff; }
QPushButton#primary:pressed { background: #2f5ed4; }
QPushButton#primary:disabled { background: #252836; color: #3a3f55; }

QPushButton#danger:hover { color: #f05252; }

/* ── inputs ── */
QComboBox, QLineEdit {
    background: #1e2130;
    border: 1px solid #2a2e3f;
    border-radius: 6px;
    padding: 6px 10px;
    color: #dde3f0;
}
QComboBox:focus, QLineEdit:focus { border-color: #3b6ef5; }
QComboBox::drop-down  { border: none; width: 24px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView {
    background: #1e2130;
    border: 1px solid #2a2e3f;
    selection-background-color: #2d3a5e;
    outline: none;
}

/* ── slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #2a2e3f;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #3b6ef5;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #fff;
    border: 2px solid #3b6ef5;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

/* ── progress bar ── */
QProgressBar {
    background: #1e2130;
    border: none;
    border-radius: 3px;
    height: 4px;
    text-visible: false;
}
QProgressBar::chunk { background: #3b6ef5; border-radius: 3px; }
QProgressBar#done::chunk   { background: #3ecf8e; }
QProgressBar#error::chunk  { background: #f05252; }

/* ── scroll ── */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a2e3f;
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ── cards ── */
#card {
    background: #1a1d27;
    border: 1px solid #2a2e3f;
    border-radius: 10px;
}
#card[status="done"]    { border-color: #1e4a36; }
#card[status="error"]   { border-color: #4a1e1e; }
#card[status="running"] { border-color: #2d3a5e; }

/* ── ext badge ── */
#badge {
    background: #252836;
    color: #4a5170;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 5px;
}

/* ── drop zone ── */
#dropzone {
    border: 2px dashed #2a2e3f;
    border-radius: 12px;
}
#dropzone:hover { border-color: #3b6ef5; }

/* ── status bar ── */
#statusbar {
    background: #1a1d27;
    border-top: 1px solid #2a2e3f;
    color: #4a5170;
    font-size: 11px;
    padding: 0 16px;
}
"""

# ─────────────────────────── helpers ─────────────────────────────────────────

def fmt_size(b: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def ffmpeg_ok() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False

def probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True)
        return float(json.loads(r.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return 0.0

def collect_videos(paths: list[str]) -> list[str]:
    """Expand any folders; return only recognised video files."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for ext in VIDEO_EXTS:
                out += [str(v) for v in Path(p).rglob(f"*{ext}")]
        elif Path(p).suffix.lower() in VIDEO_EXTS:
            out.append(p)
    return out

# ─────────────────────────── worker ──────────────────────────────────────────

class Worker(QObject):
    progress  = Signal(float)   # 0–100
    finished  = Signal(bool, str)  # ok, error_msg

    def __init__(self, src, dst, codec, crf, preset, cancel_evt):
        super().__init__()
        self.src, self.dst     = src, dst
        self.codec, self.crf   = codec, crf
        self.preset            = preset
        self.cancel            = cancel_evt

    def run(self):
        cmd = ["ffmpeg", "-y", "-i", self.src,
               "-c:v", self.codec, "-crf", str(self.crf), "-preset", self.preset,
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
               self.dst]
        dur = probe_duration(self.src)
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                                universal_newlines=True, bufsize=1)
        for line in proc.stderr:
            if self.cancel.is_set():
                proc.terminate(); proc.wait()
                self.finished.emit(False, "cancelled")
                return
            if dur > 0 and "time=" in line:
                try:
                    ts = line.split("time=")[1].split()[0]
                    h, m, s = ts.split(":")
                    pct = min((int(h)*3600 + int(m)*60 + float(s)) / dur * 100, 99)
                    self.progress.emit(pct)
                except Exception:
                    pass
        proc.wait()
        if proc.returncode == 0:
            self.progress.emit(100)
            self.finished.emit(True, "")
        else:
            self.finished.emit(False, f"ffmpeg exited with code {proc.returncode}")

# ─────────────────────────── card widget ─────────────────────────────────────

class CardWidget(QFrame):
    remove_requested = Signal(object)  # emits self

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path       = path
        self.orig_size  = os.path.getsize(path)
        self.status     = "queued"
        self.cancel_evt = threading.Event()
        self._thread    = None
        self._worker    = None

        self.setObjectName("card")
        self.setProperty("status", "queued")

        # ── layout ────────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # top row
        top = QHBoxLayout()
        top.setSpacing(10)

        ext = Path(path).suffix.lower().lstrip(".")
        badge = QLabel(ext.upper())
        badge.setObjectName("badge")
        badge.setFixedWidth(36)
        badge.setAlignment(Qt.AlignCenter)
        top.addWidget(badge, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(2)
        self._name_lbl = QLabel(os.path.basename(path))
        self._name_lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._name_lbl.setStyleSheet("color: #dde3f0;")
        self._meta_lbl = QLabel(fmt_size(self.orig_size))
        self._meta_lbl.setStyleSheet("color: #4a5170; font-size: 11px;")
        info.addLayout(info, 0) if False else None  # placeholder
        info.addWidget(self._name_lbl)
        info.addWidget(self._meta_lbl)
        top.addLayout(info, 1)

        right = QVBoxLayout()
        right.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self._status_lbl = QLabel("QUEUED")
        self._status_lbl.setStyleSheet("color: #4a5170; font-size: 10px; font-weight: 700;")
        self._status_lbl.setAlignment(Qt.AlignRight)
        rm = QPushButton("✕")
        rm.setObjectName("danger")
        rm.setFixedSize(28, 28)
        rm.setStyleSheet("border: none; background: transparent; font-size: 13px; color: #3a3f55;")
        rm.clicked.connect(lambda: self.remove_requested.emit(self))
        right.addWidget(self._status_lbl)
        right.addWidget(rm, 0, Qt.AlignRight)
        top.addLayout(right)

        root.addLayout(top)

        # progress bar (hidden until encoding)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.hide()
        root.addWidget(self._bar)

    # ── public API ────────────────────────────────────────────────────────────

    def start_encode(self, dst: str, codec: str, crf: int, preset: str):
        self._dst = dst
        self._set_status("running", "ENCODING…", "#f5a623")
        self._bar.show()

        self._worker = Worker(self.path, dst, codec, crf, preset, self.cancel_evt)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._bar.setValue)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def cancel(self):
        self.cancel_evt.set()

    # ── private ───────────────────────────────────────────────────────────────

    def _on_finished(self, ok: bool, err: str):
        if self.cancel_evt.is_set():
            self._set_status("cancelled", "CANCELLED", "#4a5170")
            return
        if ok:
            out_size = os.path.getsize(self._dst) if os.path.exists(self._dst) else 0
            pct = (1 - out_size / self.orig_size) * 100 if self.orig_size else 0
            self._meta_lbl.setText(
                f"{fmt_size(self.orig_size)}  →  {fmt_size(out_size)}   "
                f"({pct:.0f}% smaller)")
            self._meta_lbl.setStyleSheet("color: #3ecf8e; font-size: 11px;")
            self._bar.setObjectName("done")
            self._bar.setStyle(self._bar.style())
            self._bar.setValue(100)
            self._set_status("done", "✓  DONE", "#3ecf8e")
        else:
            self._meta_lbl.setText(f"Error: {err}")
            self._meta_lbl.setStyleSheet("color: #f05252; font-size: 11px;")
            self._bar.setObjectName("error")
            self._bar.setStyle(self._bar.style())
            self._set_status("error", "✗  ERROR", "#f05252")

    def _set_status(self, prop: str, text: str, color: str):
        self.status = prop
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 10px; font-weight: 700;")
        self.setProperty("status", prop)
        self.setStyle(self.style())  # force QSS re-evaluation

# ─────────────────────────── drop zone ───────────────────────────────────────

class DropZone(QWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)

        icon = QLabel("📂")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 40px; background: transparent; border: none;")

        msg = QLabel("Drop videos here  or  click  Add files")
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet("color: #4a5170; font-size: 13px; background: transparent; border: none;")

        sub = QLabel("MP4 · MKV · MOV · AVI · WebM · FLV · WMV  and more")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #2a2e3f; font-size: 11px; background: transparent; border: none;")

        lay.addWidget(icon)
        lay.addWidget(msg)
        lay.addWidget(sub)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self.files_dropped.emit(collect_videos(paths))

    def mousePressEvent(self, e):
        self.files_dropped.emit([])   # signal with empty list → open dialog

# ─────────────────────────── main window ─────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VideoShrink")
        self.resize(1040, 700)
        self.setMinimumSize(860, 560)
        self.setAcceptDrops(True)

        self._cards: list[CardWidget] = []
        self._queue_running = False

        self._build_ui()

        if not ffmpeg_ok():
            QMessageBox.critical(self, "FFmpeg not found",
                "FFmpeg is required but was not found in PATH.\n\n"
                "  macOS:    brew install ffmpeg\n"
                "  Ubuntu:   sudo apt install ffmpeg\n"
                "  Windows:  https://ffmpeg.org/download.html")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._mk_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._mk_queue_panel(), 1)
        body.addWidget(self._mk_sidebar())

        outer.addLayout(body, 1)
        outer.addWidget(self._mk_statusbar())

    def _mk_header(self) -> QWidget:
        hdr = QWidget(); hdr.setObjectName("header"); hdr.setFixedHeight(54)
        h = QHBoxLayout(hdr); h.setContentsMargins(20, 0, 20, 0)
        title = QLabel("VideoShrink")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #dde3f0;")
        sub   = QLabel("FFmpeg-powered video compression")
        sub.setStyleSheet("color: #4a5170; font-size: 12px;")
        h.addWidget(title)
        h.addWidget(sub)
        h.addStretch()
        return hdr

    def _mk_queue_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        # toolbar
        tb = QWidget(); tb.setFixedHeight(50)
        h = QHBoxLayout(tb); h.setContentsMargins(16, 0, 16, 0)
        lbl = QLabel("Queue"); lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        btn_add   = QPushButton("+ Add files"); btn_add.setFixedHeight(32)
        btn_clear = QPushButton("Clear all");   btn_clear.setFixedHeight(32)
        btn_add.clicked.connect(self._open_dialog)
        btn_clear.clicked.connect(self._clear_all)
        h.addWidget(lbl); h.addStretch()
        h.addWidget(btn_clear); h.addSpacing(6); h.addWidget(btn_add)
        v.addWidget(tb)

        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color: #2a2e3f;")
        v.addWidget(div)

        # stacked: drop zone or scroll list
        self._stack = QStackedWidget()

        # page 0 — empty drop zone
        self._dropzone = DropZone()
        self._dropzone.files_dropped.connect(self._on_files)
        self._stack.addWidget(self._dropzone)

        # page 1 — card list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(12, 12, 12, 12)
        self._card_layout.setSpacing(6)
        self._card_layout.addStretch()
        scroll.setWidget(self._card_container)
        self._stack.addWidget(scroll)

        v.addWidget(self._stack, 1)
        return panel

    def _mk_sidebar(self) -> QWidget:
        side = QWidget(); side.setObjectName("sidebar"); side.setFixedWidth(300)
        v = QVBoxLayout(side); v.setContentsMargins(20, 20, 20, 20); v.setSpacing(4)

        def section(txt):
            lbl = QLabel(txt); lbl.setObjectName("section")
            v.addSpacing(16); v.addWidget(lbl)

        def row_lbl(txt, style="color: #8892aa;"):
            l = QLabel(txt); l.setStyleSheet(style); return l

        # codec
        section("CODEC")
        self._codec_box = QComboBox()
        self._codec_box.addItems(list(CODECS.keys()))
        v.addWidget(self._codec_box)

        # CRF
        section("QUALITY  (CRF)")
        crf_row = QHBoxLayout()
        crf_row.addWidget(row_lbl("lower = better quality"))
        self._crf_val = QLabel("24")
        self._crf_val.setStyleSheet("color: #3b6ef5; font-size: 22px; font-weight: 700;")
        self._crf_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        crf_row.addWidget(self._crf_val)
        v.addLayout(crf_row)
        self._crf = QSlider(Qt.Horizontal)
        self._crf.setRange(0, 51); self._crf.setValue(24)
        self._crf.valueChanged.connect(lambda x: self._crf_val.setText(str(x)))
        v.addWidget(self._crf)
        hint = QHBoxLayout()
        hint.addWidget(row_lbl("← lossless", "color: #2a2e3f; font-size: 10px;"))
        hint.addStretch()
        hint.addWidget(row_lbl("worst →", "color: #2a2e3f; font-size: 10px;"))
        v.addLayout(hint)

        # preset
        section("ENCODING SPEED")
        self._preset_box = QComboBox(); self._preset_box.addItems(PRESETS)
        self._preset_box.setCurrentText("medium")
        v.addWidget(self._preset_box)
        v.addWidget(row_lbl("slower = smaller file, same quality",
                             "color: #2a2e3f; font-size: 10px;"))

        # output folder
        section("OUTPUT FOLDER")
        out_row = QHBoxLayout(); out_row.setSpacing(6)
        self._outdir = QLineEdit(); self._outdir.setPlaceholderText("same folder as source")
        btn_browse = QPushButton("…"); btn_browse.setFixedWidth(36)
        btn_browse.clicked.connect(self._browse)
        out_row.addWidget(self._outdir); out_row.addWidget(btn_browse)
        v.addLayout(out_row)

        # suffix
        section("FILENAME SUFFIX")
        self._suffix = QLineEdit("_shrunk")
        v.addWidget(self._suffix)
        v.addWidget(row_lbl("e.g.  video_shrunk.mp4",
                             "color: #2a2e3f; font-size: 10px;"))

        v.addStretch()

        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color: #2a2e3f;"); v.addWidget(div)
        v.addSpacing(12)

        self._start_btn = QPushButton("▶  Start transcoding")
        self._start_btn.setObjectName("primary"); self._start_btn.setFixedHeight(44)
        self._start_btn.clicked.connect(self._start)
        v.addWidget(self._start_btn)
        v.addSpacing(8)

        self._stop_btn = QPushButton("■  Stop all")
        self._stop_btn.setFixedHeight(36); self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        v.addWidget(self._stop_btn)

        return side

    def _mk_statusbar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("statusbar"); bar.setFixedHeight(30)
        h = QHBoxLayout(bar); h.setContentsMargins(16, 0, 16, 0)
        self._status_lbl = QLabel("Ready — add videos to begin.")
        h.addWidget(self._status_lbl)
        return bar

    # ── drag-and-drop on main window ──────────────────────────────────────────

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self._add_videos(collect_videos(paths))

    # ── actions ───────────────────────────────────────────────────────────────

    def _open_dialog(self):
        exts = " ".join(f"*{x}" for x in VIDEO_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select videos", "", f"Video files ({exts});;All files (*.*)")
        if paths:
            self._add_videos(paths)

    def _on_files(self, paths: list[str]):
        if not paths:          # clicked empty drop zone
            self._open_dialog()
        else:
            self._add_videos(paths)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d: self._outdir.setText(d)

    def _add_videos(self, paths: list[str]):
        existing = {c.path for c in self._cards}
        for p in paths:
            if p in existing: continue
            card = CardWidget(p)
            card.remove_requested.connect(self._remove_card)
            # insert before the stretch
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._cards.append(card)
        self._sync_view()
        self._sync_status()

    def _remove_card(self, card: CardWidget):
        card.cancel()
        self._card_layout.removeWidget(card)
        card.deleteLater()
        self._cards.remove(card)
        self._sync_view()
        self._sync_status()

    def _clear_all(self):
        for c in list(self._cards): self._remove_card(c)

    def _sync_view(self):
        self._stack.setCurrentIndex(0 if not self._cards else 1)

    def _start(self):
        pending = [c for c in self._cards if c.status == "queued"]
        if not pending:
            QMessageBox.information(self, "Nothing to do",
                "No queued videos." if not self._cards
                else "All videos have already been processed.")
            return
        self._queue_running = True
        self._start_btn.setEnabled(False)
        self._start_btn.setText("⏳  Processing…")
        self._stop_btn.setEnabled(True)
        threading.Thread(target=self._run_queue, args=(pending,), daemon=True).start()

    def _stop(self):
        self._queue_running = False
        for c in self._cards:
            if c.status == "running": c.cancel()
        self._finish_ui()

    def _run_queue(self, pending: list[CardWidget]):
        codec  = CODECS[self._codec_box.currentText()]
        crf    = self._crf.value()
        preset = self._preset_box.currentText()
        outdir = self._outdir.text().strip()
        suffix = self._suffix.text()

        for card in pending:
            if not self._queue_running: break

            src = Path(card.path)
            dst_dir = Path(outdir) if outdir else src.parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = str(dst_dir / f"{src.stem}{suffix}{src.suffix}")

            # blocks until this card's QThread finishes
            done_evt = threading.Event()
            card._thread_done = done_evt

            def on_done(ok, err, c=card, ev=done_evt):
                ev.set()
                self._sync_status()

            # start_encode must be called from the main thread
            from PySide6.QtCore import QMetaObject, Q_ARG
            card.start_encode(dst, codec, crf, preset)
            card._worker.finished.connect(on_done)

            # wait for this card to finish before starting the next
            done_evt.wait()

        self._queue_running = False
        # update UI from main thread
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._finish_ui)

    def _finish_ui(self):
        self._start_btn.setEnabled(True)
        self._start_btn.setText("▶  Start transcoding")
        self._stop_btn.setEnabled(False)
        self._sync_status()

    def _sync_status(self):
        n      = len(self._cards)
        done   = sum(1 for c in self._cards if c.status == "done")
        errs   = sum(1 for c in self._cards if c.status == "error")
        queued = sum(1 for c in self._cards if c.status == "queued")
        if n == 0:
            txt = "Ready — add videos to begin."
        elif self._queue_running:
            txt = f"Encoding…   {done}/{n} done   ·   {queued} queued"
        else:
            parts = [f"{n} video(s)"]
            if done:  parts.append(f"{done} done")
            if errs:  parts.append(f"{errs} error(s)")
            txt = "   ·   ".join(parts)
        self._status_lbl.setText(txt)


# ─────────────────────────── entry point ─────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
