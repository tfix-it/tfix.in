"""
Normalize customer uploads (pdf / jpg / png / docx / pptx / ppt) into a PDF,
so pricing.get_page_count() and calc_price() can work the same way for
every file type.
"""
import subprocess
import uuid
from pathlib import Path

import img2pdf

from . import config

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".docx", ".pptx", ".ppt"}

# Extensions LibreOffice --headless --convert-to pdf can handle directly.
LIBREOFFICE_EXTENSIONS = {".docx", ".pptx", ".ppt"}


def save_upload(file_bytes: bytes, original_filename: str) -> Path:
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext or '(none)'}")
    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(file_bytes)
    return dest


def convert_to_pdf(src_path: Path) -> Path:
    """Return a path to a PDF version of src_path, converting if necessary."""
    ext = src_path.suffix.lower()

    if ext == ".pdf":
        return src_path

    if ext in (".jpg", ".jpeg", ".png"):
        pdf_path = src_path.with_suffix(".pdf")
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(str(src_path)))
        return pdf_path

    if ext in LIBREOFFICE_EXTENSIONS:
        # LibreOffice headless writes <name>.pdf into --outdir. Works the
        # same way for Word docs and PowerPoint decks — soffice picks the
        # right filter based on the input extension.
        subprocess.run(
            [
                config.LIBREOFFICE_PATH,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(src_path.parent),
                str(src_path),
            ],
            check=True,
            timeout=120,
        )
        pdf_path = src_path.with_suffix(".pdf")
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice conversion did not produce a PDF")
        return pdf_path

    raise ValueError(f"Unsupported file type: {ext}")
