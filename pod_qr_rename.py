"""Read the waybill QR code in each image in a folder and rename the image to it.

Works for any courier: the waybill is the first QR code that is not one of our
own labels (codes starting with IGNORE_PREFIXES, e.g. product QR codes).

Usage:
    python pod_qr_rename.py <folder> [--output <folder>] [--dry-run] [--recursive]

With --output, images are copied to that folder under the waybill name and the
originals are left untouched. Images whose waybill code cannot be read are left
untouched and listed in the log.
"""
import argparse
import csv
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import zxingcpp

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# QR codes starting with these are our own labels, never waybills
# (e.g. product code NBTUIPOPL0250G). Add a prefix here to ignore more.
IGNORE_PREFIXES = ("NB", "LS")


def load_image(path):
    # cv2.imread fails on non-ASCII Windows paths, so decode from bytes instead
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _variants(img):
    """Yield progressively more processed versions of the image to try decoding."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    yield gray
    h, w = gray.shape
    # Large phone photos often decode better downscaled; tiny images upscaled
    if max(h, w) > 2000:
        yield cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    if max(h, w) < 800:
        yield cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    yield cv2.filter2D(gray, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
    yield cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 51, 10)


def read_codes(img):
    """Return all QR code values found in the image (barcodes are ignored)."""
    found = {}
    for variant in _variants(img):
        for r in zxingcpp.read_barcodes(variant, formats=zxingcpp.BarcodeFormat.QRCode):
            if r.text and r.text.strip():
                found[r.text.strip()] = True
    return list(found)


def pick_waybill(codes):
    """Return the first code that is not one of our own labels, or None."""
    for code in codes:
        if not code.upper().startswith(IGNORE_PREFIXES):
            return code
    return None


def safe_filename(text):
    name = INVALID_CHARS.sub("_", text).strip(" .")
    return name[:150] or None


def unique_target(folder, stem, ext, current, claimed):
    target = folder / f"{stem}{ext}"
    n = 1
    while (target.exists() and target.resolve() != current.resolve()) or target in claimed:
        target = folder / f"{stem}_{n}{ext}"
        n += 1
    return target


def process(folder, dry_run=False, recursive=False, output=None):
    glob_pattern = "**/*" if recursive else "*"
    files = sorted(p for p in folder.glob(glob_pattern)
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTS
                   and not (output and p.is_relative_to(output)))
    if not files:
        print(f"No images found in {folder}")
        return

    if output and not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    log_path = (output if output and not dry_run else folder) / f"qr_rename_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
    renamed = failed = 0
    rows = []
    claimed = set()  # names already assigned this run (needed for --dry-run)

    for i, path in enumerate(files, 1):
        img = load_image(path)
        if img is None:
            status, qr, new_name, codes = "UNREADABLE_IMAGE", "", "", []
        else:
            codes = read_codes(img)
            qr = pick_waybill(codes)
            stem = safe_filename(qr) if qr else None
            if not stem:
                status, qr, new_name = "NO_WAYBILL_FOUND", "", ""
            else:
                target = unique_target(output or path.parent, stem, path.suffix.lower(), path, claimed)
                claimed.add(target)
                new_name = target.name
                if target.resolve() == path.resolve():
                    status = "ALREADY_NAMED"
                elif dry_run:
                    status = "DRY_RUN"
                elif output:
                    shutil.copy2(path, target)
                    status = "COPIED"
                else:
                    path.rename(target)
                    status = "RENAMED"

        if status in ("RENAMED", "COPIED", "DRY_RUN"):
            renamed += 1
        elif status != "ALREADY_NAMED":
            failed += 1
        print(f"[{i}/{len(files)}] {path.name} -> {new_name or '-'}  ({status})")
        rows.append([path.name, qr, new_name, status, " | ".join(codes)])

    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["original_file", "waybill", "new_file", "status", "all_qr_found"])
        writer.writerows(rows)

    verb = "Would rename" if dry_run else ("Copied" if output else "Renamed")
    print(f"\n{verb}: {renamed}   Failed: {failed}   Total: {len(files)}")
    print(f"Log saved: {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Rename images using their waybill QR code.")
    parser.add_argument("folder", nargs="?", help="Folder containing the images")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be renamed without renaming")
    parser.add_argument("--output", help="Copy renamed images here instead of renaming in place")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders")
    args = parser.parse_args()

    folder_arg = args.folder or input("Enter folder path: ").strip().strip('"')
    folder = Path(folder_arg)
    if not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")
    output = Path(args.output).resolve() if args.output else None
    process(folder.resolve(), dry_run=args.dry_run, recursive=args.recursive, output=output)


if __name__ == "__main__":
    main()
