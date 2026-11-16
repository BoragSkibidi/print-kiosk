"""Flash drive helpers. Mounting is done by udiskie; we only read /proc/mounts."""
import os
import shutil
from datetime import datetime
from pathlib import Path

MAX_FILES = 300
MAX_DEPTH = 3


def drives(cfg):
    roots = [r.rstrip("/") + "/" for r in cfg["usb"]["mount_roots"]]
    found = []

    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        lines = []

    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        mp = parts[1].replace("\\040", " ")
        if any(mp.startswith(r) for r in roots):
            found.append(Path(mp))

    fake = cfg["usb"].get("fake_drive")
    if fake and Path(fake).is_dir():
        found.append(Path(fake).resolve())

    return found


def list_files(cfg):
    exts = set(cfg["limits"]["allowed_ext"])
    files = []
    for drive in drives(cfg):
        for root, dirs, names in os.walk(drive):
            rel = Path(root).relative_to(drive)
            if len(rel.parts) >= MAX_DEPTH:
                dirs[:] = []
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))

            for n in sorted(names):
                p = Path(root) / n
                if n.startswith(".") or p.suffix.lower() not in exts:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue

                files.append({
                    "path": str(p), 
                    "name": n,
                    "folder": "" if str(rel) == "." else str(rel), 
                    "size": size
                })

                if len(files) >= MAX_FILES:
                    return files
                
    return files


def validate(cfg, path):
    exts = set(cfg["limits"]["allowed_ext"])
    p = Path(path).resolve()
    for drive in drives(cfg):
        if p.is_relative_to(drive.resolve()) and p.is_file() and p.suffix.lower() in exts:
            return p
    raise ValueError("File not found on the flash drive")


def save(cfg, pdf):
    ds = drives(cfg)
    if not ds:
        raise RuntimeError("Flash drive not found")
    name = datetime.now().strftime("scan_%Y%m%d_%H%M%S.pdf")
    shutil.copyfile(pdf, ds[0] / name)
    os.sync()
    return name
