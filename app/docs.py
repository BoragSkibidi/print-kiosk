"""Convert uploads to PDF and count pages, using LibreOffice and pypdf."""
import subprocess
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
    cmd = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(src)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        raise DocError("File took too long to process")
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise DocError("Could not convert this file")
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


def images_to_pdf(images, out: Path, dpi: int) -> Path:
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in images]
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=float(dpi))
    return out
