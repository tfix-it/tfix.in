"""
Windows print submission + printer discovery.

Requires:
  - pip install pywin32
  - SumatraPDF.exe available at config.SUMATRA_PATH
    (https://www.sumatrapdfreader.org/download-free-pdf-viewer — grab the
    portable .exe, no install needed, just point SUMATRA_PATH at it)
"""
import subprocess

import win32print

from . import config


def list_windows_printers():
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def get_printer_status(printer_name: str) -> str:
    try:
        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
        finally:
            win32print.ClosePrinter(handle)
    except Exception:
        return "unknown"

    status = info["Status"]
    if status == 0:
        return "ready"
    if status & win32print.PRINTER_STATUS_OFFLINE:
        return "offline"
    if status & win32print.PRINTER_STATUS_ERROR:
        return "error"
    if status & win32print.PRINTER_STATUS_PAPER_OUT:
        return "paper_out"
    return "busy"


def print_pdf(pdf_path: str, printer_name: str, duplex: bool, copies: int,
               orientation: str = "portrait", duplex_edge: str = "long",
               pages_range: str = "", scale_mode: str = "fit"):
    orientation = orientation if orientation in ("portrait", "landscape") else "portrait"
    if duplex:
        duplex_edge = duplex_edge if duplex_edge in ("long", "short") else "long"
        sides = "duplexshort" if duplex_edge == "short" else "duplexlong"
    else:
        sides = "simplex"

    # SumatraPDF's -print-settings only understands "noscale" (actual size)
    # or "fit"/"shrink" (fit to printable area) — there's no arbitrary
    # percentage keyword. A custom % is recorded on the job for the record,
    # but at print time we fall back to "fit" so the job doesn't error out.
    scale_token = "noscale" if scale_mode == "actual" else "fit"

    settings_parts = [f"{copies}x", sides, orientation, scale_token]
    if pages_range:
        settings_parts.append(pages_range)
    settings = ",".join(settings_parts)
    subprocess.run(
        [
            config.SUMATRA_PATH,
            "-print-to", printer_name,
            "-print-settings", settings,
            "-silent",
            pdf_path,
        ],
        check=True,
        timeout=120,
    )
