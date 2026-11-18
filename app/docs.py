"""Convert uploads to PDF and count pages, using LibreOffice and pypdf."""
import os
import subprocess
import uuid
from pathlib import Path

from pypdf import PdfReader

OFFICE_EXT = {".doc", ".docx", ".ppt", ".pptx", ".odt", ".odp"}

class DocError(Exception):
    pass

def to_pdf(src: Path, timeout: int) -> Path:
    ext = src.suffix.lower()

    if ext == ".pdf":
        return src
    if ext not in OFFICE_EXT:
        raise DocError("Unsupported file type")
    
    outdir = src.parent

    # Own profile dir so it never clashes with a LibreOffice you have open.
    profile = outdir.parent / "lo-profile"

    # PDF Conversion Command for OFFICE_EXT Filetype
    cmd = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(src)]

    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        raise DocError("File took too long to process")
    except subprocess.CalledProcessError:
        raise DocError("Could not convert this file")
    except FileNotFoundError:
        raise DocError("Could not find this file")
        
    pdf = src.with_suffix(".pdf")

    if not pdf.exists():
        raise DocError("Could not convert this file")
    return pdf


def count_pages(pdf: Path) -> int:
    try:
        reader = PdfReader(pdf)
        if reader.is_encrypted:
            raise DocError("Password-protected files are not supported")
        n = len(reader.pages)
    except DocError:
        raise
    except Exception:
        raise DocError("This file is damaged or not a real PDF")
    if n == 0:
        raise DocError("This file has no pages")
    return n


def images_to_pdf(images: Path, out: Path, dpi: int) -> Path:
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in images]
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=float(dpi))
    return out


def render_page(pdf: Path, index: int, out: Path, size: int, timeout: int = 30) -> Path:
    """Render page `index` (0-based) of `pdf` to a JPEG whose longest side is `size` px.
    Uses pdftoppm (Arch: poppler, Pi OS: poppler-utils)."""
    base = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:6]}")      # pdftoppm adds .jpg
    n = str(index + 1)
    cmd = ["pdftoppm", "-f", n, "-l", n, "-singlefile", "-scale-to", str(size),
           "-jpeg", "-jpegopt", "quality=80", str(pdf), str(base)]
    try:
        out.parent.mkdir(exist_ok=True)       # no parents: if the job was cancelled, fail
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        os.replace(f"{base}.jpg", out)        # atomic: never serve a half-written file
    except (OSError, subprocess.SubprocessError):
        Path(f"{base}.jpg").unlink(missing_ok=True)
        raise DocError("Preview not available")
    return out