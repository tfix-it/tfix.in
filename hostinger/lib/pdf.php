<?php
/**
 * PDF helpers that work on plain shared hosting (no Python, no Ghostscript):
 * page counting, JPEG/PNG -> PDF, and page-range parsing.
 */
declare(strict_types=1);

/**
 * Count the pages of a PDF. Reads every object, including objects packed in
 * compressed object streams (PDF 1.5+), keeps the latest definition of each
 * object (incremental updates), then takes the page tree's /Count.
 */
function pdf_page_count(string $path): int
{
    $data = @file_get_contents($path);
    if ($data === false || !str_starts_with(ltrim(substr($data, 0, 1024)), '%PDF-')) {
        throw new HttpError(400, "This file isn't a valid PDF.");
    }
    $objects = [];
    if (preg_match_all('/(?<![0-9])(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj\b/s', $data, $m, PREG_SET_ORDER)) {
        foreach ($m as $hit) {
            $objects[(int) $hit[1]] = $hit[3];   // later definitions win
        }
    }
    if (preg_match('#/Encrypt\s*(<<|\d+\s+\d+\s+R)#', $data)) {
        throw new HttpError(400, 'Password-protected PDFs can’t be printed. Please upload an unlocked copy.');
    }
    // Unpack object streams.
    foreach ($objects as $body) {
        if (!preg_match('#/Type\s*/ObjStm\b#', $body)) {
            continue;
        }
        $stream = pdf_stream_bytes($body);
        if ($stream === null || !preg_match('#/First\s+(\d+)#', $body, $f) || !preg_match('#/N\s+(\d+)#', $body, $n)) {
            continue;
        }
        $first = (int) $f[1];
        $header = preg_split('/\s+/', trim(substr($stream, 0, $first)));
        $pairs = [];
        for ($i = 0; $i + 1 < count($header) && count($pairs) < (int) $n[1]; $i += 2) {
            $pairs[] = [(int) $header[$i], (int) $header[$i + 1]];
        }
        foreach ($pairs as $i => [$num, $offset]) {
            $end = isset($pairs[$i + 1]) ? $pairs[$i + 1][1] : strlen($stream) - $first;
            if (!isset($objects[$num]) || !preg_match('#\bstream\b#', $objects[$num])) {
                $objects[$num] = substr($stream, $first + $offset, $end - $offset);
            }
        }
    }
    $max = 0;
    $leafPages = 0;
    foreach ($objects as $body) {
        $dict = pdf_dict_part($body);
        if (preg_match('#/Type\s*/Pages\b#', $dict) && preg_match('#/Count\s+(\d+)#', $dict, $c)) {
            $max = max($max, (int) $c[1]);
        } elseif (preg_match('#/Type\s*/Page\b(?!s)#', $dict)) {
            $leafPages++;
        }
    }
    $count = $max ?: $leafPages;
    if ($count < 1) {
        throw new HttpError(400, "Couldn't read the pages in this PDF. Try saving it again as PDF.");
    }
    return $count;
}

/** The dictionary part of an object, without stream data (avoids false matches in binary). */
function pdf_dict_part(string $body): string
{
    $pos = strpos($body, 'stream');
    return $pos === false ? $body : substr($body, 0, $pos);
}

function pdf_stream_bytes(string $body): ?string
{
    if (!preg_match('/stream\r?\n/', $body, $m, PREG_OFFSET_CAPTURE)) {
        return null;
    }
    $start = $m[0][1] + strlen($m[0][0]);
    $endPos = strrpos($body, 'endstream');
    if ($endPos === false || $endPos < $start) {
        return null;
    }
    $raw = substr($body, $start, $endPos - $start);
    $dict = substr($body, 0, $m[0][1]);
    if (preg_match('#/Length\s+(\d+)(?!\s+\d+\s+R)#', $dict, $len) && (int) $len[1] <= strlen($raw)) {
        $raw = substr($raw, 0, (int) $len[1]);
    }
    if (preg_match('#/FlateDecode#', $dict)) {
        $out = @gzuncompress($raw);
        if ($out === false) {
            $out = @gzinflate(substr($raw, 2)) ?: @gzinflate($raw);
        }
        return $out === false ? null : $out;
    }
    return preg_match('#/Filter#', $dict) ? null : $raw;
}

/** Turn a JPEG or PNG into a one-page PDF (same idea as img2pdf in the Python version). */
function image_to_pdf(string $imagePath, string $pdfPath): void
{
    $info = @getimagesize($imagePath);
    if (!$info || !in_array($info[2], [IMAGETYPE_JPEG, IMAGETYPE_PNG], true)) {
        throw new HttpError(400, "This image couldn't be read. Upload a JPG or PNG.");
    }
    [$w, $h] = $info;
    if ($w < 1 || $h < 1 || $w * $h > 60_000_000) {
        throw new HttpError(400, 'Image is too large to print.');
    }
    if ($info[2] === IMAGETYPE_PNG) {
        if (!function_exists('imagecreatefrompng')) {
            throw new HttpError(500, 'PNG support (GD) is not enabled on the server.');
        }
        $src = @imagecreatefrompng($imagePath);
        if (!$src) {
            throw new HttpError(400, "This PNG couldn't be read.");
        }
        $flat = imagecreatetruecolor($w, $h);
        imagefill($flat, 0, 0, imagecolorallocate($flat, 255, 255, 255));
        imagecopy($flat, $src, 0, 0, 0, 0, $w, $h);
        ob_start();
        imagejpeg($flat, null, 92);
        $jpeg = (string) ob_get_clean();
        $channels = 3;
    } else {
        $jpeg = (string) file_get_contents($imagePath);
        $channels = $info['channels'] ?? 3;
    }
    $colorSpace = match ($channels) { 1 => '/DeviceGray', 4 => '/DeviceCMYK', default => '/DeviceRGB' };
    $decode = $channels === 4 ? '/Decode [1 0 1 0 1 0 1 0] ' : '';
    $pw = round($w * 72 / 96, 2);
    $ph = round($h * 72 / 96, 2);
    $content = "q $pw 0 0 $ph 0 0 cm /Im0 Do Q";
    $objs = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 $pw $ph] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /XObject /Subtype /Image /Width $w /Height $h /ColorSpace $colorSpace /BitsPerComponent 8 $decode/Filter /DCTDecode /Length " . strlen($jpeg) . " >>\nstream\n$jpeg\nendstream",
        '<< /Length ' . strlen($content) . " >>\nstream\n$content\nendstream",
    ];
    $out = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
    $offsets = [];
    foreach ($objs as $i => $o) {
        $offsets[] = strlen($out);
        $out .= ($i + 1) . " 0 obj\n$o\nendobj\n";
    }
    $xref = strlen($out);
    $out .= "xref\n0 " . (count($objs) + 1) . "\n0000000000 65535 f \n";
    foreach ($offsets as $off) {
        $out .= sprintf("%010d 00000 n \n", $off);
    }
    $out .= 'trailer << /Size ' . (count($objs) + 1) . " /Root 1 0 R >>\nstartxref\n$xref\n%%EOF\n";
    if (file_put_contents($pdfPath, $out) === false) {
        throw new HttpError(500, 'Could not save the converted file.');
    }
}

/**
 * Parse a page selection like "1-5,8,11-15" against the document's page
 * count. Empty means every page. Returns the sorted, de-duplicated list and
 * the canonical string the customer page also produces.
 */
function parse_pages_range(string $spec, int $total): array
{
    $spec = trim($spec);
    if ($spec === '') {
        return [range(1, $total), ''];
    }
    $pages = [];
    foreach (explode(',', $spec) as $part) {
        $part = trim($part);
        if (preg_match('/^(\d+)$/', $part, $m)) {
            $a = $b = (int) $m[1];
        } elseif (preg_match('/^(\d+)\s*-\s*(\d+)$/', $part, $m)) {
            [$a, $b] = [(int) $m[1], (int) $m[2]];
        } else {
            throw new HttpError(400, 'Page range must look like 1-5, 8, 11-15');
        }
        if ($a < 1 || $b < $a || $b > $total) {
            throw new HttpError(400, "Page range must stay within 1-$total");
        }
        for ($p = $a; $p <= $b; $p++) {
            $pages[$p] = true;
        }
    }
    $list = array_keys($pages);
    sort($list);
    // canonical form
    $ranges = [];
    $start = $prev = $list[0];
    foreach (array_slice($list, 1) as $p) {
        if ($p === $prev + 1) { $prev = $p; continue; }
        $ranges[] = $start === $prev ? "$start" : "$start-$prev";
        $start = $prev = $p;
    }
    $ranges[] = $start === $prev ? "$start" : "$start-$prev";
    $canonical = implode(',', $ranges);
    return [$list, count($list) === $total ? '' : $canonical];
}
