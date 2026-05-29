#!/usr/bin/env python3
"""
VideoShrink — Desktop video transcoder
Reduces file size using FFmpeg. Drag-and-drop queue, per-file progress.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import os
import json
from pathlib import Path

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = "#111318"
SURFACE  = "#1a1d24"
CARD     = "#21252e"
BORDER   = "#2d3240"
ACCENT   = "#4f8ef7"
GREEN    = "#3ecf8e"
AMBER    = "#f5a623"
RED      = "#f05252"
TXT      = "#e2e8f0"
TXT_DIM  = "#6b7694"
TXT_MID  = "#94a3b8"

VIDEO_EXTS = {".mp4",".mkv",".mov",".avi",".webm",".flv",".wmv",".m4v",".ts",".mts"}

CODEC_LABELS = [
    "H.265 / HEVC  —  best compression",
    "H.264 / AVC   —  best compatibility",
]
CODEC_MAP = {
    CODEC_LABELS[0]: "libx265",
    CODEC_LABELS[1]: "libx264",
}
PRESETS = ["ultrafast","superfast","veryfast","faster","fast",
           "medium","slow","slower","veryslow"]

# ── FFmpeg ────────────────────────────────────────────────────────────────────
def ffmpeg_ok():
    try:
        return subprocess.run(["ffmpeg","-version"],capture_output=True).returncode == 0
    except FileNotFoundError:
        return False

def get_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe","-v","quiet","-print_format","json","-show_format",path],
            capture_output=True, text=True)
        return float(json.loads(r.stdout).get("format",{}).get("duration",0))
    except Exception:
        return 0.0

def transcode(src, dst, codec, crf, preset, on_progress, cancel_evt):
    cmd = ["ffmpeg","-y","-i",src,
           "-c:v",codec,"-crf",str(crf),"-preset",preset,
           "-c:a","aac","-b:a","128k","-movflags","+faststart",dst]
    dur = get_duration(src)
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                            universal_newlines=True, bufsize=1)
    for line in proc.stderr:
        if cancel_evt.is_set():
            proc.terminate(); proc.wait()
            return False, "cancelled"
        if dur > 0 and "time=" in line:
            try:
                ts = line.split("time=")[1].split()[0]
                h,m,s = ts.split(":")
                on_progress(min((int(h)*3600+int(m)*60+float(s))/dur*100, 99))
            except Exception:
                pass
    proc.wait()
    if proc.returncode == 0:
        on_progress(100); return True, None
    return False, f"ffmpeg exit {proc.returncode}"

def fmt_size(b):
    for u in ("B","KB","MB","GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

# ── Queue item ────────────────────────────────────────────────────────────────
class Item:
    def __init__(self, path):
        self.path      = path
        self.name      = os.path.basename(path)
        self.orig_size = os.path.getsize(path)
        self.status    = "queued"
        self.out_path  = None
        self.out_size  = None
        self.err       = None
        self.cancel    = threading.Event()
        # widgets
        self.frame       = None
        self.status_lbl  = None
        self.meta_lbl    = None
        self.prog_var    = None
        self.prog_bar    = None
        self.prog_frame  = None
        self.card        = None

# ── App ───────────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        Root = TkinterDnD.Tk if HAS_DND else tk.Tk
        self.root = Root()
        self.root.title("VideoShrink")
        self.root.geometry("1000x700")
        self.root.minsize(860, 560)
        self.root.configure(bg=BG)

        self.items   = []
        self.running = False

        self.v_codec  = tk.StringVar(value=CODEC_LABELS[0])
        self.v_crf    = tk.IntVar(value=24)
        self.v_preset = tk.StringVar(value="medium")
        self.v_outdir = tk.StringVar(value="")
        self.v_suffix = tk.StringVar(value="_shrunk")

        self._styles()
        self._ui()

        if not ffmpeg_ok():
            messagebox.showerror("FFmpeg not found",
                "FFmpeg is required but was not found in PATH.\n\n"
                "macOS:   brew install ffmpeg\n"
                "Ubuntu:  sudo apt install ffmpeg\n"
                "Windows: https://ffmpeg.org/download.html")

    # ── ttk styles ─────────────────────────────────────────────────────────────
    def _styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TProgressbar",
                    troughcolor=BORDER, background=ACCENT,
                    thickness=3, borderwidth=0)
        s.configure("TScrollbar",
                    troughcolor=SURFACE, background=BORDER,
                    borderwidth=0, arrowcolor=TXT_DIM, width=8)
        s.configure("TCombobox",
                    fieldbackground=CARD, background=CARD,
                    foreground=TXT, selectbackground=BORDER,
                    insertcolor=TXT, borderwidth=0, relief="flat")
        s.map("TCombobox",
              fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", TXT)],
              background=[("readonly", CARD)])

    # ── UI skeleton ────────────────────────────────────────────────────────────
    def _ui(self):
        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=SURFACE, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="VideoShrink", bg=SURFACE, fg=TXT,
                 font=("Helvetica", 15, "bold"), padx=20).pack(side="left")
        tk.Label(hdr, text="FFmpeg-powered video compression",
                 bg=SURFACE, fg=TXT_DIM,
                 font=("Helvetica", 10)).pack(side="left", pady=18)
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── body (queue | divider | settings) ─────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        self._queue_col(body)
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")
        self._settings_col(body)

        # ── status bar ────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", side="bottom")
        sb = tk.Frame(self.root, bg=SURFACE, height=30)
        sb.pack(fill="x", side="bottom")
        sb.pack_propagate(False)
        self.status_lbl = tk.Label(sb, text="Ready — add videos to begin.",
                                    bg=SURFACE, fg=TXT_DIM,
                                    font=("Helvetica", 9), anchor="w", padx=14)
        self.status_lbl.pack(fill="both", expand=True)

    # ── queue column ───────────────────────────────────────────────────────────
    def _queue_col(self, parent):
        col = tk.Frame(parent, bg=BG)
        col.pack(side="left", fill="both", expand=True)

        # toolbar
        tb = tk.Frame(col, bg=BG, pady=10, padx=14)
        tb.pack(fill="x")
        tk.Label(tb, text="Queue", bg=BG, fg=TXT,
                 font=("Helvetica", 12, "bold")).pack(side="left")
        self._btn(tb, "Clear all", self._clear, small=True).pack(side="right")
        self._btn(tb, "+ Add files", self._add_files,
                  accent=True, small=True).pack(side="right", padx=(0, 8))

        tk.Frame(col, bg=BORDER, height=1).pack(fill="x")

        # scrollable list
        wrap = tk.Frame(col, bg=BG)
        wrap.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._list = tk.Frame(self._canvas, bg=BG)
        self._win  = self._canvas.create_window((0,0), window=self._list, anchor="nw")

        self._list.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._win, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # empty state
        self._empty = tk.Frame(self._list, bg=BG)
        self._empty.pack(fill="both", expand=True, pady=100)
        tk.Label(self._empty, text="📂", bg=BG, fg=BORDER,
                 font=("Helvetica", 36)).pack()
        tk.Label(self._empty,
                 text="Drop videos here  or  click  + Add files",
                 bg=BG, fg=TXT_DIM,
                 font=("Helvetica", 11)).pack(pady=(10,4))
        tk.Label(self._empty,
                 text="MP4 · MKV · MOV · AVI · WebM · FLV · WMV · and more",
                 bg=BG, fg=BORDER,
                 font=("Helvetica", 9)).pack()

        if HAS_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        else:
            for w in [self._empty] + list(self._empty.winfo_children()):
                w.bind("<Button-1>", lambda e: self._add_files())

    # ── settings column ────────────────────────────────────────────────────────
    def _settings_col(self, parent):
        col = tk.Frame(parent, bg=SURFACE, width=290)
        col.pack(side="right", fill="y")
        col.pack_propagate(False)

        pad = tk.Frame(col, bg=SURFACE, padx=20, pady=16)
        pad.pack(fill="both", expand=True)

        def lbl(txt, dim=False):
            tk.Label(pad, text=txt, bg=SURFACE,
                     fg=TXT_DIM if dim else TXT_MID,
                     font=("Helvetica", 8, "bold")).pack(anchor="w", pady=(14,3))

        def sep():
            tk.Frame(pad, bg=BORDER, height=1).pack(fill="x", pady=(10,0))

        # codec
        lbl("CODEC")
        cb = ttk.Combobox(pad, textvariable=self.v_codec,
                          values=CODEC_LABELS, state="readonly",
                          font=("Helvetica", 10))
        cb.pack(fill="x")

        # CRF
        sep()
        crf_hdr = tk.Frame(pad, bg=SURFACE)
        crf_hdr.pack(fill="x", pady=(10,0))
        tk.Label(crf_hdr, text="QUALITY  (CRF)", bg=SURFACE,
                 fg=TXT_MID, font=("Helvetica", 8, "bold")).pack(side="left")
        self._crf_num = tk.Label(crf_hdr, text="24", bg=SURFACE,
                                  fg=ACCENT, font=("Helvetica", 22, "bold"))
        self._crf_num.pack(side="right")

        tk.Scale(pad, from_=0, to=51, variable=self.v_crf,
                 orient="horizontal", bg=SURFACE, fg=TXT,
                 troughcolor=BORDER, activebackground=ACCENT,
                 highlightthickness=0, sliderrelief="flat", showvalue=False,
                 command=lambda v: self._crf_num.config(text=str(int(float(v))))
                 ).pack(fill="x", pady=(2,0))

        hint = tk.Frame(pad, bg=SURFACE)
        hint.pack(fill="x")
        tk.Label(hint, text="← better quality", bg=SURFACE,
                 fg=BORDER, font=("Helvetica", 8)).pack(side="left")
        tk.Label(hint, text="smaller file →", bg=SURFACE,
                 fg=BORDER, font=("Helvetica", 8)).pack(side="right")

        # preset
        sep()
        lbl("SPEED / COMPRESSION")
        ttk.Combobox(pad, textvariable=self.v_preset, values=PRESETS,
                     state="readonly", font=("Helvetica", 10)).pack(fill="x")
        tk.Label(pad, text="slower preset = smaller file, same quality",
                 bg=SURFACE, fg=BORDER, font=("Helvetica", 8)).pack(anchor="w", pady=(3,0))

        # output dir
        sep()
        lbl("OUTPUT FOLDER")
        outrow = tk.Frame(pad, bg=SURFACE)
        outrow.pack(fill="x")
        tk.Entry(outrow, textvariable=self.v_outdir,
                 bg=CARD, fg=TXT_MID, insertbackground=TXT,
                 relief="flat", font=("Helvetica", 9), bd=0
                 ).pack(side="left", fill="x", expand=True, ipady=7, padx=(0,6))
        self._btn(outrow, "…", self._browse, small=True).pack(side="right")
        tk.Label(pad, text="blank = save next to source",
                 bg=SURFACE, fg=BORDER, font=("Helvetica", 8)).pack(anchor="w", pady=(3,0))

        # suffix
        sep()
        lbl("FILENAME SUFFIX")
        tk.Entry(pad, textvariable=self.v_suffix,
                 bg=CARD, fg=TXT, insertbackground=TXT,
                 relief="flat", font=("Helvetica", 10), bd=0
                 ).pack(fill="x", ipady=7)
        tk.Label(pad, text="e.g.  video_shrunk.mp4",
                 bg=SURFACE, fg=BORDER, font=("Helvetica", 8)).pack(anchor="w", pady=(3,0))

        # buttons
        tk.Frame(pad, bg=SURFACE).pack(fill="both", expand=True)
        sep()

        self._start_btn = self._btn(pad, "▶  Start transcoding",
                                     self._start, accent=True)
        self._start_btn.pack(fill="x", pady=(14,8))

        self._stop_btn = self._btn(pad, "■  Stop all", self._stop)
        self._stop_btn.config(state="disabled")
        self._stop_btn.pack(fill="x")

    # ── widget helpers ─────────────────────────────────────────────────────────
    def _btn(self, parent, text, cmd, accent=False, small=False):
        return tk.Button(parent, text=text, command=cmd,
                         bg=ACCENT if accent else CARD,
                         fg=BG if accent else TXT_MID,
                         activebackground="#3a78e0" if accent else BORDER,
                         activeforeground=BG if accent else TXT,
                         font=("Helvetica", 9 if small else 10, "bold"),
                         relief="flat", bd=0, cursor="hand2",
                         padx=10 if small else 14,
                         pady=5 if small else 10)

    # ── file management ────────────────────────────────────────────────────────
    def _add_files(self):
        types = [("Video files"," ".join(f"*{x}" for x in VIDEO_EXTS)),
                 ("All files","*.*")]
        for p in filedialog.askopenfilenames(title="Select videos", filetypes=types):
            self._enqueue(p)

    def _on_drop(self, event):
        for f in self.root.tk.splitlist(event.data):
            f = f.strip("{}")
            if os.path.isfile(f) and Path(f).suffix.lower() in VIDEO_EXTS:
                self._enqueue(f)
            elif os.path.isdir(f):
                for ext in VIDEO_EXTS:
                    for v in Path(f).glob(f"*{ext}"):
                        self._enqueue(str(v))

    def _browse(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d: self.v_outdir.set(d)

    def _enqueue(self, path):
        if any(it.path == path for it in self.items): return
        it = Item(path)
        self.items.append(it)
        self._draw_card(it)
        self._sync_empty()
        self._sync_status()

    def _clear(self):
        for it in self.items:
            it.cancel.set()
            if it.frame: it.frame.destroy()
        self.items.clear()
        self._sync_empty()
        self._sync_status()

    # ── card drawing ───────────────────────────────────────────────────────────
    def _draw_card(self, it: Item):
        self._empty.pack_forget()

        it.frame = tk.Frame(self._list, bg=BG)
        it.frame.pack(fill="x", padx=12, pady=4)

        it.card = tk.Frame(it.frame, bg=CARD,
                           highlightbackground=BORDER, highlightthickness=1)
        it.card.pack(fill="x")

        # main row
        row = tk.Frame(it.card, bg=CARD, padx=14, pady=12)
        row.pack(fill="x")

        # left: ext tag + info
        left = tk.Frame(row, bg=CARD)
        left.pack(side="left", fill="x", expand=True)

        ext = Path(it.path).suffix.lower().lstrip(".")
        tk.Label(left, text=f" {ext.upper()} ",
                 bg=BORDER, fg=TXT_MID,
                 font=("Helvetica", 8, "bold"),
                 padx=4, pady=2
                 ).pack(side="left", anchor="n", padx=(0,10), pady=2)

        info = tk.Frame(left, bg=CARD)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=it.name, bg=CARD, fg=TXT,
                 font=("Helvetica", 10, "bold"), anchor="w").pack(anchor="w")
        it.meta_lbl = tk.Label(info, text=fmt_size(it.orig_size),
                                bg=CARD, fg=TXT_DIM,
                                font=("Helvetica", 9), anchor="w")
        it.meta_lbl.pack(anchor="w")

        # right: status + remove
        right = tk.Frame(row, bg=CARD)
        right.pack(side="right")

        it.status_lbl = tk.Label(right, text="QUEUED",
                                  bg=CARD, fg=TXT_DIM,
                                  font=("Helvetica", 8, "bold"), anchor="e")
        it.status_lbl.pack(anchor="e", pady=(0,6))

        tk.Button(right, text="✕", bg=CARD, fg=TXT_DIM,
                  activebackground=CARD, activeforeground=RED,
                  font=("Helvetica", 10), relief="flat", bd=0,
                  cursor="hand2",
                  command=lambda i=it: self._remove(i)
                  ).pack(anchor="e")

        # progress bar row (hidden until encoding)
        it.prog_frame = tk.Frame(it.card, bg=CARD, padx=14, pady=0)
        it.prog_var   = tk.DoubleVar(value=0)
        it.prog_bar   = ttk.Progressbar(it.prog_frame,
                                         variable=it.prog_var,
                                         mode="determinate")
        it.prog_bar.pack(fill="x")
        # NOTE: it.prog_frame is NOT packed yet — shown on encode start

    def _remove(self, it: Item):
        it.cancel.set()
        if it.frame: it.frame.destroy()
        if it in self.items: self.items.remove(it)
        self._sync_empty()
        self._sync_status()

    def _sync_empty(self):
        if not self.items:
            self._empty.pack(fill="both", expand=True, pady=100)

    # ── transcoding ────────────────────────────────────────────────────────────
    def _start(self):
        pending = [i for i in self.items if i.status == "queued"]
        if not pending:
            messagebox.showinfo("Nothing to do",
                "No queued videos found." if not self.items
                else "All videos already processed.")
            return
        self.running = True
        self._start_btn.config(state="disabled", text="⏳  Processing…")
        self._stop_btn.config(state="normal")
        threading.Thread(target=self._worker, daemon=True).start()

    def _stop(self):
        self.running = False
        for it in self.items:
            if it.status == "running": it.cancel.set()
        self._finish_ui()

    def _worker(self):
        codec  = CODEC_MAP[self.v_codec.get()]
        crf    = self.v_crf.get()
        preset = self.v_preset.get()
        outdir = self.v_outdir.get().strip()
        suffix = self.v_suffix.get()

        for it in self.items:
            if not self.running: break
            if it.status != "queued": continue

            src = Path(it.path)
            dst_dir = Path(outdir) if outdir else src.parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            it.out_path = str(dst_dir / f"{src.stem}{suffix}{src.suffix}")

            it.status = "running"
            self.root.after(0, lambda i=it: self._ui_running(i))

            ok, err = transcode(
                it.path, it.out_path, codec, crf, preset,
                lambda pct, i=it: self.root.after(0, lambda p=pct, i2=i: self._ui_progress(i2, p)),
                it.cancel)

            if it.cancel.is_set():
                it.status = "cancelled"
            elif ok:
                it.status  = "done"
                it.out_size = (os.path.getsize(it.out_path)
                               if os.path.exists(it.out_path) else 0)
            else:
                it.status = "error"
                it.err = err

            self.root.after(0, lambda i=it: self._ui_done(i))
            self.root.after(0, self._sync_status)

        self.running = False
        self.root.after(0, self._finish_ui)

    # ── card state updates (always called from main thread via after) ──────────
    def _ui_running(self, it: Item):
        it.status_lbl.config(text="ENCODING…", fg=AMBER)
        it.prog_frame.pack(fill="x", padx=14, pady=(0,10))

    def _ui_progress(self, it: Item, pct: float):
        it.prog_var.set(pct)

    def _ui_done(self, it: Item):
        if it.status == "done":
            it.status_lbl.config(text="✓  DONE", fg=GREEN)
            pct = (1 - it.out_size / it.orig_size) * 100 if it.orig_size else 0
            it.meta_lbl.config(
                text=(f"{fmt_size(it.orig_size)}  →  "
                      f"{fmt_size(it.out_size)}   "
                      f"({pct:.0f}% smaller)"),
                fg=GREEN)
            it.prog_var.set(100)
            # green tint on card border
            it.card.config(highlightbackground="#2d4a38")
        elif it.status == "error":
            it.status_lbl.config(text="✗  ERROR", fg=RED)
            it.meta_lbl.config(text=f"Error: {it.err}", fg=RED)
            it.card.config(highlightbackground="#4a2d2d")
        elif it.status == "cancelled":
            it.status_lbl.config(text="CANCELLED", fg=TXT_DIM)

    def _finish_ui(self):
        self._start_btn.config(state="normal", text="▶  Start transcoding")
        self._stop_btn.config(state="disabled")

    # ── status bar ─────────────────────────────────────────────────────────────
    def _sync_status(self):
        n      = len(self.items)
        done   = sum(1 for i in self.items if i.status == "done")
        errs   = sum(1 for i in self.items if i.status == "error")
        queued = sum(1 for i in self.items if i.status == "queued")
        saved  = sum((i.orig_size - (i.out_size or i.orig_size))
                     for i in self.items if i.status == "done" and i.out_size)
        if n == 0:
            txt = "Ready — add videos to begin."
        elif self.running:
            txt = f"Encoding…   {done}/{n} done   ·   {queued} queued"
        else:
            parts = [f"{n} video(s) in queue"]
            if done:  parts.append(f"{done} done")
            if errs:  parts.append(f"{errs} error(s)")
            if saved > 0: parts.append(f"saved {fmt_size(int(saved))}")
            txt = "   ·   ".join(parts)
        self.status_lbl.config(text=txt)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    App().run()
