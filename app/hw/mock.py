"""Fake hardware for developing on a PC.

Test error paths without a printer:
  touch out/OFFLINE   -> machine goes out of service (rm to recover)
  touch out/JAM       -> next print fails like a paper jam
"""
import shutil
import time
from datetime import datetime
from pathlib import Path


class FilePrinter:
    def __init__(self, c):
        self.out = Path(c["out_dir"])
        self.out.mkdir(parents=True, exist_ok=True)

    def status(self):
        if (self.out / "OFFLINE").exists():
            return False, "Printer offline (mock)"
        return True, ""

    def print(self, pdf, mode, pages, copies=1):
        time.sleep(min(1 + pages * copies * 0.3, 6))
        if (self.out / "JAM").exists():
            raise RuntimeError("Paper jam (mock)")
        extra = f"_x{copies}" if copies > 1 else ""
        dest = self.out / f"{datetime.now():%Y%m%d_%H%M%S}_{mode}_{pages}p{extra}.pdf"
        shutil.copyfile(pdf, dest)


class MockScanner:
    def __init__(self, c):
        self.dpi = c.get("resolution", 150)
        self.n = 0

    def scan(self, out: Path, on_progress=None):
        from PIL import Image, ImageDraw
        for pct in range(0, 101, 10):          # like scanimage --progress
            if on_progress:
                on_progress(pct)
            time.sleep(0.15)
        self.n += 1
        w, h = int(8.27 * self.dpi), int(11.69 * self.dpi)   # A4
        img = Image.new("RGB", (w, h), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([40, 40, w - 40, h - 40], outline="black", width=4)
        d.text((80, 80), f"MOCK SCAN #{self.n}  {datetime.now():%H:%M:%S}", fill="black")
        img.save(out)
        return out