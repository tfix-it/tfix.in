"""One durable print gate shared by Android, web, auto-print and MCP."""
from pathlib import Path
from . import db, config, printing

class PrintBlocked(ValueError):
    pass

SCHEMA = '''
CREATE TABLE IF NOT EXISTS print_dispatches (
 job_id INTEGER PRIMARY KEY, printer TEXT NOT NULL, actor TEXT NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_audit (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, paid INTEGER NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL);
'''

def initialize():
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)

def confirm_payment(job_id: int, paid: bool):
    with db.get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise PrintBlocked('Job not found')
        if not paid and conn.execute('SELECT 1 FROM print_dispatches WHERE job_id=?', (job_id,)).fetchone():
            raise PrintBlocked('Cannot revoke payment after dispatch; reconcile manually.')
        conn.execute('UPDATE jobs SET paid=?, updated_at=? WHERE id=?', (int(paid), db.now(), job_id))
        conn.execute('INSERT INTO payment_audit(job_id,paid,actor,created_at) VALUES(?,?,?,?)',
                     (job_id, int(paid), 'staff_manual', db.now()))
    return db.get_job(job_id)

def submit(job_id: int, printer_name: str, actor: str = 'staff', auto: bool = False):
    # Commit claim BEFORE talking to Windows. Never automatically retry an ambiguous failure.
    with db.get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise PrintBlocked('Job not found')
        job = dict(row)
        if job['paid'] != 1:
            raise PrintBlocked('Payment must be confirmed by staff before printing.')
        if job['status'] != 'queued':
            raise PrintBlocked('Only queued jobs can print; check existing print history.')
        if conn.execute('SELECT 1 FROM print_dispatches WHERE job_id=?', (job_id,)).fetchone():
            raise PrintBlocked('Already dispatched or outcome uncertain. Inspect the Windows queue; do not retry.')
        printer = conn.execute('SELECT * FROM printers WHERE windows_printer_name=? AND enabled=1', (printer_name,)).fetchone()
        if not printer:
            raise PrintBlocked('Printer is not registered and enabled.')
        path = (config.UPLOAD_DIR / job['pdf_filename']).resolve()
        if not path.is_relative_to(config.UPLOAD_DIR.resolve()) or not path.is_file() or job['files_deleted']:
            raise PrintBlocked('Print file unavailable.')
        conn.execute('INSERT INTO print_dispatches VALUES(?,?,?,?,?,?)',
                     (job_id, printer_name, actor, 'dispatching', db.now(), db.now()))
        conn.execute("UPDATE jobs SET status='printing', auto_printed=?, updated_at=? WHERE id=?", (int(auto), db.now(), job_id))
    try:
        printing.print_pdf(str(path), printer_name, bool(job['doubleside']), job['copies'],
            orientation=job.get('orientation', 'portrait'), duplex_edge=job.get('duplex_edge','long'),
            pages_range=job.get('pages_range',''), scale_mode=job.get('scale_mode','fit'))
    except Exception as exc:
        with db.get_conn() as conn:
            conn.execute("UPDATE print_dispatches SET state='uncertain', updated_at=? WHERE job_id=?", (db.now(), job_id))
        raise PrintBlocked('Print outcome uncertain. Check Windows queue and physical output before staff recovery.') from exc
    with db.get_conn() as conn:
        conn.execute("UPDATE print_dispatches SET state='submitted', updated_at=? WHERE job_id=?", (db.now(), job_id))
    return db.get_job(job_id)
