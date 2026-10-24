"""Read QR codes from images in a folder and rename each image to its QR value.

Usage:
    python pod_qr_rename.py <folder> [--dry-run] [--recursive]

Images whose QR code cannot be read are left untouched and listed in the log.
"""
import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_detector = cv2.QRCodeDetector()
_aruco_detector = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None


def load_image(path):
    # cv2.imread fails on non-ASCII Windows paths, so decode from bytes instead
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _variants(img):
    """Yield progressively more processed versions of the image to try decoding."""
    yield img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    yield gray
    h, w = gray.shape
    # Large phone photos: downscale; tiny images: upscale
    if max(h, w) > 2000:
        scale = 2000 / max(h, w)
        yield cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if max(h, w) < 800:
        yield cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    yield cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    yield cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 51, 10)
    sharpened = cv2.filter2D(gray, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
    yield sharpened
    for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
        yield cv2.rotate(gray, rot)


def read_qr(img):
    """Return the first non-empty QR value found in the image, or None."""
    detectors = [d for d in (_detector, _aruco_detector) if d is not None]
    for variant in _variants(img):
        for det in detectors:
            try:
                ok, values, _, _ = det.detectAndDecodeMulti(variant)
                if ok:
                    for v in values:
                        if v and v.strip():
                            return v.strip()
                value, _, _ = det.detectAndDecode(variant)
                if value and value.strip():
                    return value.strip()
            except cv2.error:
                continue
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


def process(folder, dry_run=False, recursive=False):
    pattern = "**/*" if recursive else "*"
    files = sorted(p for p in folder.glob(pattern)
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if not files:
        print(f"No images found in {folder}")
        return

    log_path = folder / f"qr_rename_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
    renamed = failed = 0
    rows = []
    claimed = set()  # names already assigned this run (needed for --dry-run)

    for i, path in enumerate(files, 1):
        img = load_image(path)
        if img is None:
            status, qr, new_name = "UNREADABLE_IMAGE", "", ""
        else:
            qr = read_qr(img)
            stem = safe_filename(qr) if qr else None
            if not stem:
                status, qr, new_name = "NO_QR_FOUND", qr or "", ""
            else:
                target = unique_target(path.parent, stem, path.suffix.lower(), path, claimed)
                claimed.add(target)
                new_name = target.name
                if target.resolve() == path.resolve():
                    status = "ALREADY_NAMED"
                elif dry_run:
                    status = "DRY_RUN"
                else:
                    path.rename(target)
                    status = "RENAMED"

        if status in ("RENAMED", "DRY_RUN"):
            renamed += 1
        elif status != "ALREADY_NAMED":
            failed += 1
        print(f"[{i}/{len(files)}] {path.name} -> {new_name or '-'}  ({status})")
        rows.append([path.name, qr, new_name, status])

    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["original_file", "qr_value", "new_file", "status"])
        writer.writerows(rows)

    verb = "Would rename" if dry_run else "Renamed"
    print(f"\n{verb}: {renamed}   Failed: {failed}   Total: {len(files)}")
    print(f"Log saved: {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Rename images using their QR code value.")
    parser.add_argument("folder", nargs="?", help="Folder containing the images")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be renamed without renaming")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders")
    args = parser.parse_args()

    folder_arg = args.folder or input("Enter folder path: ").strip().strip('"')
    folder = Path(folder_arg)
    if not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")
    process(folder, dry_run=args.dry_run, recursive=args.recursive)


if __name__ == "__main__":
    main()
