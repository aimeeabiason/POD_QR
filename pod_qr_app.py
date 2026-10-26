"""POD photo renamer - Windows app.

Reads the waybill QR code in each photo in the source folder, renames the photo
to the waybill and moves it to <destination>/03-MARCH/ based on when the
photo was taken. Photos that cannot be read stay in the source folder and are
listed in the error log.
"""
import json
import shutil
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image

from pod_qr_rename import (IMAGE_EXTS, load_image, pick_waybill, read_codes,
                           safe_filename, unique_target)

# When built as an .exe, keep settings next to the .exe, not in its temp unpack folder
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SETTINGS_FILE = APP_DIR / "pod_qr_settings.json"
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306
# Fixed English names so folder names don't depend on the PC's language setting
MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
          "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")


def photo_date(path):
    """Date the photo was taken (EXIF), falling back to the file's date."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            value = exif.get_ifd(0x8769).get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME)
        if value:
            return datetime.strptime(value.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    stat = path.stat()
    return datetime.fromtimestamp(min(stat.st_ctime, stat.st_mtime))


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data):
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("POD Photo Renamer")
        self.minsize(640, 420)

        settings = load_settings()
        self.source = tk.StringVar(value=settings.get("source", ""))
        self.dest = tk.StringVar(value=settings.get("dest", ""))
        self.log_path = tk.StringVar(value=settings.get("log", "LOG.TXT"))
        self.subfolders = tk.BooleanVar(value=settings.get("subfolders", False))

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Folder Path", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._row(frm, 1, "Source Folder :", self.source, self.browse_source)
        ttk.Checkbutton(frm, text="Include subfolders", variable=self.subfolders).grid(
            row=2, column=1, sticky="w", pady=(0, 3))
        self._row(frm, 3, "Destination Folder :", self.dest, self.browse_dest)
        self._row(frm, 4, "Log error path :", self.log_path, self.browse_log)

        self.run_btn = ttk.Button(frm, text="RENAME & SAVE PHOTO", command=self.start)
        self.run_btn.grid(row=5, column=1, columnspan=2, sticky="e", pady=10)

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew")
        self.status = ttk.Label(frm, text="Ready")
        self.status.grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 4))

        self.output = tk.Text(frm, height=12, state="disabled", wrap="none")
        self.output.grid(row=8, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(8, weight=1)

    def _row(self, parent, row, label, var, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="Browse...", command=command).grid(row=row, column=2, padx=(6, 0), pady=3)

    def browse_source(self):
        path = filedialog.askdirectory(title="Select source folder", initialdir=self.source.get() or None)
        if path:
            self.source.set(str(Path(path)))

    def browse_dest(self):
        path = filedialog.askdirectory(title="Select destination folder", initialdir=self.dest.get() or None)
        if path:
            self.dest.set(str(Path(path)))

    def browse_log(self):
        path = filedialog.asksaveasfilename(title="Error log file", defaultextension=".txt",
                                            initialfile="LOG.TXT", filetypes=[("Text file", "*.txt")])
        if path:
            self.log_path.set(str(Path(path)))

    def write(self, line):
        self.output.configure(state="normal")
        self.output.insert("end", line + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def resolve_log(self, dest):
        # A bare file name like LOG.TXT is saved in the destination folder
        log = Path(self.log_path.get().strip() or "LOG.TXT")
        return log if log.is_absolute() else dest / log

    def start(self):
        source = Path(self.source.get().strip().strip('"'))
        dest = Path(self.dest.get().strip().strip('"'))
        if not self.source.get().strip() or not source.is_dir():
            messagebox.showerror("Source Folder", "Please select a valid source folder.")
            return
        if not self.dest.get().strip():
            messagebox.showerror("Destination Folder", "Please select a destination folder.")
            return
        if source.resolve() == dest.resolve():
            messagebox.showerror("Destination Folder", "Destination must be different from the source folder.")
            return

        subfolders = self.subfolders.get()
        save_settings({"source": str(source), "dest": str(dest), "log": self.log_path.get(),
                       "subfolders": subfolders})
        self.run_btn.configure(state="disabled")
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        threading.Thread(target=self.process, args=(source, dest, self.resolve_log(dest), subfolders),
                         daemon=True).start()

    def ui(self, func, *args):
        self.after(0, func, *args)

    def process(self, source, dest, log_path, subfolders=False):
        candidates = source.rglob("*") if subfolders else source.iterdir()
        # Skip the destination if it sits inside the source, so filed photos aren't processed again
        dest_resolved = dest.resolve()
        files = sorted(p for p in candidates if p.is_file() and p.suffix.lower() in IMAGE_EXTS
                       and not p.resolve().is_relative_to(dest_resolved))
        total = len(files)
        self.ui(self.progress.configure, {"maximum": max(total, 1), "value": 0})
        if not total:
            self.ui(self.finish, "No photos found in the source folder.", 0, 0, None)
            return

        dest.mkdir(parents=True, exist_ok=True)
        errors = []
        saved = 0
        claimed = set()
        for i, path in enumerate(files, 1):
            name = path.relative_to(source)  # includes the subfolder, if any
            self.ui(self.status.configure, {"text": f"Processing {i}/{total}: {name}"})
            try:
                img = load_image(path)
                if img is None:
                    raise ValueError("cannot open image")
                codes = read_codes(img)
                stem = safe_filename(pick_waybill(codes) or "")
                if not stem:
                    found = f" (found: {' | '.join(codes)})" if codes else ""
                    raise ValueError(f"no waybill QR code found{found}")
                taken = photo_date(path)
                folder = dest / f"{taken:%m}-{MONTHS[taken.month - 1]}"
                folder.mkdir(parents=True, exist_ok=True)
                target = unique_target(folder, stem, path.suffix.lower(), path, claimed)
                claimed.add(target)
                shutil.move(str(path), str(target))
                saved += 1
                self.ui(self.write, f"OK     {name} -> {target.relative_to(dest)}")
            except Exception as e:
                errors.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {path}  ERROR: {e}")
                self.ui(self.write, f"ERROR  {name}: {e}")
            self.ui(self.progress.configure, {"value": i})

        if errors:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(errors) + "\n")
            except Exception as e:
                self.ui(self.write, f"Could not write log {log_path}: {e}")
        self.ui(self.finish, f"Done. Saved: {saved}   Errors: {len(errors)}   Total: {total}",
                saved, len(errors), log_path if errors else None)

    def finish(self, message, saved, failed, log_path):
        self.status.configure(text=message)
        self.run_btn.configure(state="normal")
        if log_path:
            message += f"\n\nPhotos with errors were left in the source folder.\nError log: {log_path}"
        messagebox.showinfo("POD Photo Renamer", message)


if __name__ == "__main__":
    App().mainloop()
