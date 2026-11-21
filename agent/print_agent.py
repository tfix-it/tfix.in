"""
TPrint print agent: runs on the shop PC and connects it to https://print.tfix.in.

The website lives on Hostinger and cannot reach into the shop. This agent
reaches out instead, over plain HTTPS, every few seconds:

  1. reports the PC's Windows printers and their status,
  2. picks up jobs staff (or auto-print, or Claude) sent to a printer:
       claim -> download PDF -> SumatraPDF -> report the result,
  3. converts customers' Word/PowerPoint uploads to PDF with LibreOffice.

No router port forwarding, tunnel or open port is needed.

Safety rules (same as the shop's print gate):
  * A job is claimed before printing; the server never hands the same job out twice.
  * If anything fails AFTER the file went to SumatraPDF, the job is reported
    "uncertain" and is never retried automatically: staff check the printer.
  * If something fails BEFORE that (download error), nothing was printed and
    the job goes back to the queue.

Run on the shop PC (see HOSTINGER.md):
    set TPRINT_AGENT_KEY=<agent_key from tprint-config.php>
    venv-agent\\Scripts\\python.exe -m agent.print_agent
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

AGENT_VERSION = "tprint-agent/3.0.0"
log = logging.getLogger("tprint-agent")


class LibreOfficeConverter:
    def __init__(self, soffice: str):
        self.soffice = soffice

    def convert(self, source: Path) -> Path:
        subprocess.run([self.soffice, "--headless", "--convert-to", "pdf", "--outdir", str(source.parent), str(source)],
                       check=True, timeout=180, capture_output=True)
        pdf = source.with_suffix(".pdf")
        if not pdf.exists():
            raise RuntimeError("LibreOffice did not produce a PDF")
        return pdf


class Agent:
    def __init__(self, server: str, key: str, printing, converter, http: httpx.Client | None = None):
        self.server = server.rstrip("/")
        self.printing = printing          # app.printing (Windows) or a test double
        self.converter = converter
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, read=120), headers={
            "Authorization": "Bearer " + key, "User-Agent": AGENT_VERSION})
        self.workdir = Path(tempfile.mkdtemp(prefix="tprint-agent-"))

    # ------------------------------------------------------------ main loop
    def run_forever(self, interval: float = 3.0):
        delay = interval
        log.info("TPrint agent started; talking to %s", self.server)
        while True:
            try:
                self.run_once()
                delay = interval
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    log.error("The server rejected the agent key. Check TPRINT_AGENT_KEY.")
                else:
                    log.warning("Server error %s: %s", e.response.status_code, e.response.text[:200])
                delay = min(delay * 2, 60)
            except httpx.TransportError as e:
                log.warning("Can't reach %s (%s); retrying", self.server, e.__class__.__name__)
                delay = min(delay * 2, 60)
            time.sleep(delay)

    def run_once(self) -> dict:
        work = self._post("/api/agent/poll", json={"agent_version": AGENT_VERSION, "printers": self.printer_report()}).json()
        for conv in work.get("conversions", []):
            self.handle_conversion(conv)
        for dispatch in work.get("dispatches", []):
            self.handle_dispatch(dispatch)
        return work

    def printer_report(self) -> list[dict]:
        report = []
        try:
            names = self.printing.list_windows_printers()
        except Exception as e:  # spooler stopped, etc.
            log.warning("Could not list printers: %s", e)
            return report
        for name in names:
            try:
                status = self.printing.get_printer_status(name)
            except Exception:
                status = "unknown"
            report.append({"name": name, "status": status})
        return report

    # ------------------------------------------------------------ printing
    def handle_dispatch(self, d: dict):
        job_id = d["job_id"]
        claim = self.http.post(f"{self.server}/api/agent/dispatches/{job_id}/claim")
        if claim.status_code == 409:
            log.info("Job %s was cancelled or already taken; skipping", job_id)
            return
        claim.raise_for_status()

        pdf = self.workdir / f"job-{job_id}.pdf"
        try:
            r = self.http.get(f"{self.server}/api/agent/jobs/{job_id}/file")
            r.raise_for_status()
            pdf.write_bytes(r.content)
        except Exception as e:
            log.error("Job %s: download failed (%s); nothing printed, returning it to the queue", job_id, e)
            self._report(job_id, {"ok": False, "not_sent": True, "error": f"Download failed: {e}"})
            return

        try:
            # From here on the outcome may be ambiguous: never retry automatically.
            self.printing.print_pdf(str(pdf), d["printer"], bool(d["doubleside"]), int(d["copies"]),
                                    orientation=d.get("orientation", "portrait"), duplex_edge=d.get("duplex_edge", "long"),
                                    pages_range=d.get("pages_range", ""), scale_mode=d.get("scale_mode", "fit"))
        except Exception as e:
            log.error("Job %s: print outcome uncertain (%s). Check the Windows queue and the printer.", job_id, e)
            self._report(job_id, {"ok": False, "error": f"Print outcome uncertain: {e}"[:250]})
        else:
            log.info("Job %s sent to %s", job_id, d["printer"])
            self._report(job_id, {"ok": True})
        finally:
            pdf.unlink(missing_ok=True)

    def _report(self, job_id: int, body: dict):
        for attempt in range(5):          # reporting is safe to repeat; printing is not
            try:
                self._post(f"/api/agent/dispatches/{job_id}/result", json=body)
                return
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                log.warning("Could not report job %s result (%s); retrying", job_id, e)
                time.sleep(2 * (attempt + 1))
        log.error("Gave up reporting job %s; the admin page will show it as still sending", job_id)

    # ------------------------------------------------------------ conversion
    def handle_conversion(self, c: dict):
        upload_id, ext = c["upload_id"], c["file_type"]
        src = self.workdir / f"{upload_id}.{ext}"
        pdf = src.with_suffix(".pdf")
        try:
            r = self.http.get(f"{self.server}/api/agent/conversions/{upload_id}/source")
            if r.status_code == 409:
                return
            r.raise_for_status()
            src.write_bytes(r.content)
            out = self.converter.convert(src)
            self._post(f"/api/agent/conversions/{upload_id}/result",
                       files={"pdf": ("converted.pdf", out.read_bytes(), "application/pdf")})
            log.info("Converted %s upload %s", ext, upload_id)
        except (httpx.TransportError, httpx.HTTPStatusError):
            raise
        except Exception as e:
            log.error("Conversion of %s failed: %s", upload_id, e)
            self._post(f"/api/agent/conversions/{upload_id}/result",
                       data={"error": "This file couldn't be converted. Please upload it as a PDF."})
        finally:
            src.unlink(missing_ok=True)
            pdf.unlink(missing_ok=True)

    def _post(self, path: str, **kwargs) -> httpx.Response:
        r = self.http.post(self.server + path, **kwargs)
        r.raise_for_status()
        return r


def main(argv=None):
    parser = argparse.ArgumentParser(description="TPrint print agent for the shop PC")
    parser.add_argument("--server", default=os.environ.get("TPRINT_SERVER", "https://print.tfix.in"))
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--once", action="store_true", help="poll one time and exit (for testing)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    key = os.environ.get("TPRINT_AGENT_KEY", "")
    if len(key) < 32:
        sys.exit("Set TPRINT_AGENT_KEY to the agent_key from tprint-config.php.")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import config, printing   # Windows printers + SumatraPDF (app/printing.py)

    agent = Agent(args.server, key, printing, LibreOfficeConverter(config.LIBREOFFICE_PATH))
    if args.once:
        print(agent.run_once())
    else:
        agent.run_forever(args.interval)


if __name__ == "__main__":
    main()
