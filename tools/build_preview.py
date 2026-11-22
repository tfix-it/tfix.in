"""
Build a single-file, click-through preview of the print.tfix.in customer page.

    python tools/build_preview.py --sample sample.pdf --out tprint-preview.html

The output is the real hostinger/index.html with every asset inlined (brand
fonts, logo, QR library, pdf.js) and a small in-page stand-in for the PHP
API, so the whole order -> UPI QR -> paid -> printing flow can be clicked
through offline. Nothing is uploaded, saved, printed or paid. The QR uses an
unroutable UPI ID (@invalid) and is labelled as a sample.
"""
import argparse
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "hostinger"
ASSETS = ROOT / "assets"


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def inline_brand_css() -> str:
    css = (ASSETS / "brand.css").read_text()
    return re.sub(r"url\(fonts/([^)]+)\)", lambda m: f"url({data_uri(ASSETS / 'fonts' / m.group(1), 'font/woff2')})", css)


MOCK = r"""
<script>
/* ---------- Preview stand-in for the print.tfix.in API (runs only in this file) ---------- */
(() => {
  const RATES = { A4: { 0: [2, 3, 10], 1: [10, 15, 10] }, A3: { 0: [5, 7, 15], 1: [15, 20, 15] } };  // default shop prices
  const PAY = { upi_id: 'preview.sample@invalid', upi_payee_name: 'T-PRINT (preview)' };
  const uploads = {}, jobs = [], perPhone = {};
  let nextId = 101;
  const hex = n => [...crypto.getRandomValues(new Uint8Array(n / 2))].map(b => b.toString(16).padStart(2, '0')).join('');
  const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
  const delay = ms => new Promise(r => setTimeout(r, ms));

  // Same rules as app/pricing.py and hostinger/lib/pricing.php.
  function printCost(pages, pps, color, duplex, copies, size, applyMin) {
    const c = RATES[size][1], b = RATES[size][0];
    const cc = { s: c[0], d: c[1] }, bc = { s: b[0], d: b[1] }, min = b[2];
    const pp = Math.ceil(pages / pps);
    const odd = duplex ? pp % 2 : 0, sheets = duplex ? Math.floor(pp / 2) : pp;
    const calc = k => [(duplex ? sheets * k.d + odd * k.s : sheets * k.s) * copies, (sheets + odd) * copies];
    let total, ts;
    if (color) [total, ts] = calc(cc);
    else { let cost; [cost, ts] = calc(bc); total = !applyMin ? cost : (pp > 1 && duplex) ? cost - bc.d + min : cost - bc.s + min; }
    return [Math.round(total * 100) / 100, ts];
  }
  function countRange(spec, total) {
    if (!spec.trim()) return total;
    const set = new Set();
    for (const part of spec.split(',')) {
      const m = part.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
      if (!m) return null;
      const a = +m[1], z = +(m[2] || m[1]);
      if (a < 1 || z < a || z > total) return null;
      for (let p = a; p <= z; p++) set.add(p);
    }
    return set.size;
  }
  async function imageToPdf(file) {
    const img = await createImageBitmap(file);
    const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
    const g = cv.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, cv.width, cv.height); g.drawImage(img, 0, 0);
    const jpeg = new Uint8Array(await (await new Promise(r => cv.toBlob(r, 'image/jpeg', 0.92))).arrayBuffer());
    const w = cv.width, h = cv.height, pw = (w * 0.75).toFixed(2), ph = (h * 0.75).toFixed(2);
    const content = `q ${pw} 0 0 ${ph} 0 0 cm /Im0 Do Q`, enc = new TextEncoder();
    const objs = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pw} ${ph}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`,
      null, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`];
    const parts = [enc.encode('%PDF-1.4\n')], offs = []; let len = parts[0].length;
    objs.forEach((o, i) => {
      offs.push(len);
      const chunk = o !== null ? [enc.encode(`${i + 1} 0 obj\n${o}\nendobj\n`)] : [
        enc.encode(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${w} /Height ${h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`),
        jpeg, enc.encode('\nendstream\nendobj\n')];
      chunk.forEach(c => { parts.push(c); len += c.length; });
    });
    let tail = `xref\n0 6\n0000000000 65535 f \n` + offs.map(o => String(o).padStart(10, '0') + ' 00000 n \n').join('') + `trailer << /Size 6 /Root 1 0 R >>\nstartxref\n${len}\n%%EOF\n`;
    parts.push(enc.encode(tail));
    return new Uint8Array(await new Blob(parts).arrayBuffer());
  }
  const view = j => ({ id: j.id, token: j.token, phone: j.phone, daily_seq: j.daily_seq, original_filename: j.original_filename,
                       status: j.status, paid: j.paid, cost: j.cost, sheets: j.sheets, copies: j.copies, color: j.color,
                       paper_size: j.paper_size, doubleside: j.doubleside, created_at: new Date().toISOString() });

  window.__tprintPreviewPdf = id => ({ data: uploads[id].bytes.slice() });

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const m = url.match(/\/api\/[^?#]*/);
    if (!m) return realFetch(input, init);
    const path = m[0], query = new URLSearchParams(url.split('?')[1] || ''), body = init.body;
    await delay(250);
    if (path === '/api/upload') {
      const f = body.get('file'), ext = (f.name.split('.').pop() || '').toLowerCase();
      if (!['pdf', 'jpg', 'jpeg', 'png', 'docx', 'pptx', 'ppt'].includes(ext)) return json({ detail: 'Unsupported file type: .' + ext }, 400);
      if (['docx', 'pptx', 'ppt'].includes(ext)) return json({ detail: 'In this preview Word/PowerPoint can’t be converted (on the live site the shop PC does it). Try a PDF or a photo.' }, 400);
      let bytes;
      try { bytes = ext === 'pdf' ? new Uint8Array(await f.arrayBuffer()) : await imageToPdf(f); }
      catch (e) { return json({ detail: "This image couldn't be read." }, 400); }
      let pages;
      try { pages = (await pdfjsLib.getDocument({ data: bytes.slice() }).promise).numPages; }
      catch (e) { return json({ detail: "This file isn't a valid PDF (or it's password-protected)." }, 400); }
      const id = hex(32);
      uploads[id] = { bytes, view: { upload_id: id, pdf_filename: id, source_filename: null, original_filename: f.name, file_type: ext, pages, status: 'ready', error: null } };
      demo.uploaded();
      return json(uploads[id].view);
    }
    if (path.startsWith('/api/uploads/')) return json(uploads[path.split('/')[3]].view);
    if (path === '/api/payment-info') return json(PAY);
    if (path === '/api/quote') {
      const phone = (body.get('phone') || '').replace(/\D/g, ''), ahead = +(body.get('files_ahead_today') || 0);
      const apply = !phone || ((perPhone[phone] || 0) + ahead) === 0;
      const [cost, sheets] = printCost(+body.get('pages'), +(body.get('pages_per_sheet') || 1), body.get('color') === 'true',
        body.get('doubleside') === 'true', +(body.get('copies') || 1), body.get('paper_size') || 'A4', apply);
      return json({ cost, sheets, min_cost_applied: apply });
    }
    if (path === '/api/jobs') {
      const phone = (body.get('phone') || '').replace(/\D/g, '');
      if (!/^\d{10}$/.test(phone)) return json({ detail: 'Enter a 10-digit mobile number' }, 400);
      const up = uploads[body.get('pdf_filename')];
      const pages = countRange(body.get('pages_range') || '', up.view.pages);
      if (!pages) return json({ detail: 'Page range must stay within 1-' + up.view.pages }, 400);
      const seq = (perPhone[phone] || 0) + 1; perPhone[phone] = seq;
      const copies = +(body.get('copies') || 1), color = body.get('color') === 'true', duplex = body.get('doubleside') === 'true';
      const [cost, sheets] = printCost(pages, +(body.get('pages_per_sheet') || 1), color, duplex, copies, body.get('paper_size') || 'A4', seq === 1);
      const job = { id: nextId++, token: hex(32), phone, daily_seq: seq, original_filename: up.view.original_filename, status: 'queued',
                    paid: 0, cost, sheets, copies, color: +color, paper_size: body.get('paper_size') || 'A4', doubleside: +duplex };
      jobs.push(job);
      demo.ordered();
      return json(view(job));
    }
    if (path === '/api/jobs/status') {
      const tokens = (query.get('tokens') || '').split(',');
      return json(jobs.filter(j => tokens.includes(j.token)).map(view));
    }
    return json({ detail: 'Not available in the preview' }, 404);
  };

  // ---------- preview bar ----------
  const demo = {};
  document.addEventListener('DOMContentLoaded', () => {
    const bar = document.createElement('div');
    bar.id = 'previewBar';
    bar.innerHTML = `<div class="pv-note"><b>Preview</b> — nothing is uploaded, saved, printed or paid.</div>
      <div class="pv-actions">
        <button type="button" id="pvSample">Use a sample PDF</button>
        <button type="button" id="pvPaid" hidden>Staff marks paid</button>
        <button type="button" id="pvReady" hidden>Mark ready</button>
      </div>`;
    document.body.appendChild(bar);
    const $ = id => document.getElementById(id);
    const refresh = () => document.dispatchEvent(new Event('visibilitychange'));   // the page re-polls its order status
    $('pvSample').onclick = () => {
      const bytes = Uint8Array.from(atob(window.__TPRINT_SAMPLE), c => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], 'sample-assignment.pdf', { type: 'application/pdf' }));
      const input = $('fileInput'); input.files = dt.files; input.dispatchEvent(new Event('change'));
    };
    $('pvPaid').onclick = () => { jobs.forEach(j => { j.paid = 1; j.status = 'printing'; }); $('pvPaid').hidden = true; $('pvReady').hidden = false; refresh(); };
    $('pvReady').onclick = () => { jobs.forEach(j => { j.status = 'ready'; }); $('pvReady').hidden = true; refresh(); };
    demo.uploaded = () => { $('pvSample').hidden = true; };
    demo.ordered = () => { $('pvSample').hidden = true; $('pvPaid').hidden = false; };
  });
  demo.uploaded = demo.ordered = () => {};
})();
</script>
<style>
  body{ padding-bottom:96px !important; }
  #previewBar{ position:fixed; left:0; right:0; bottom:0; z-index:200; display:flex; flex-wrap:wrap; align-items:center; justify-content:center;
    gap:8px 14px; padding:10px 16px calc(10px + env(safe-area-inset-bottom)); background:rgba(26,28,31,.96); border-top:1px solid var(--line-2);
    -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px); font:13px/1.35 var(--f-b); color:var(--muted); }
  #previewBar b{ color:var(--white); font-family:var(--f-m); font-weight:500; letter-spacing:.12em; text-transform:uppercase; font-size:11.5px; }
  #previewBar .pv-actions{ display:flex; gap:8px; }
  #previewBar button{ font:600 13px/1 var(--f-b); color:var(--white); background:transparent; border:1px solid var(--white); border-radius:999px; padding:9px 14px; cursor:pointer; }
  #previewBar button:hover{ background:var(--white); color:var(--on-yellow); }
  .pay-card{ position:relative; }
  .pay-card::before{ content:'Sample QR — preview only, do not pay'; display:block; margin:-6px auto 12px; width:max-content; max-width:100%;
    font:500 11px/1.3 var(--f-m); letter-spacing:.06em; color:var(--on-yellow); background:var(--white); border-radius:6px; padding:4px 8px; }
</style>
"""


def build(sample: Path, out: Path):
    html = (ROOT / "index.html").read_text()
    pdfjs = (ASSETS / "pdfjs/pdf.min.js").read_text()
    worker = (ASSETS / "pdfjs/pdf.worker.min.js").read_text()
    qr = (ASSETS / "qrcode.js").read_text()

    def swap(old, new):
        nonlocal html
        assert html.count(old) == 1, old[:70]
        html = html.replace(old, new)

    swap('<link rel="stylesheet" href="assets/brand.css">', f"<style>{inline_brand_css()}</style>")
    swap('<link rel="icon" href="assets/brand/favicon.svg" type="image/svg+xml">',
         f'<link rel="icon" href="{data_uri(ASSETS / "brand/favicon.svg", "image/svg+xml")}">')
    html = re.sub(r'<link rel="preload"[^>]*>\n', "", html)
    swap('<script src="assets/pdfjs/pdf.min.js"></script>',
         f"<script>{pdfjs}</script>\n<script type=\"text/plain\" id=\"pdfjsWorker\">{worker}</script>")
    swap('<script src="assets/qrcode.js"></script>', f"<script>{qr}</script>\n<script>window.__TPRINT_SAMPLE='{base64.b64encode(sample.read_bytes()).decode()}';</script>\n{MOCK}")
    swap("'assets/pdfjs/pdf.worker.min.js'",
         "URL.createObjectURL(new Blob([document.getElementById('pdfjsWorker').textContent], { type: 'text/javascript' }))")
    swap("pdfjsLib.getDocument(`${API}/api/uploads/${uploadInfo.upload_id}/pdf`)", "pdfjsLib.getDocument(window.__tprintPreviewPdf(uploadInfo.upload_id))")
    swap('src="assets/brand/tfix-cusat.svg"', f'src="{data_uri(ASSETS / "brand/tfix-cusat.svg", "image/svg+xml")}"')
    swap("<title>T-PRINT · T FIX Auto Print</title>", "<title>T-PRINT · preview</title>")
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=Path, required=True, help="a small PDF offered by the 'Use a sample PDF' button")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    build(args.sample, args.out)
