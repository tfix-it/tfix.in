<?php
/**
 * Print cost: a line-for-line port of app/pricing.py (itself from the shop's
 * original pdfcostcalc.py), including its rules:
 *  - the minimum-charge floor applies to B&W jobs only, and
 *  - it is waived from a phone number's 2nd job of the day onward.
 */
declare(strict_types=1);

function get_rates(string $size, bool $color): ?array
{
    return row('SELECT * FROM pricing WHERE paper_size=? AND color=?', [$size, (int) $color]);
}

function calc_cost(int $sheets, bool $doubleside, int $copies, int $odd, array $costs): array
{
    if ($doubleside) {
        $cost = $sheets * $costs['doubleside'] + $odd * $costs['singleside'];
    } else {
        $cost = $sheets * $costs['singleside'];
    }
    $totalSheets = ($sheets + $odd) * $copies;
    return [$cost * $copies, $totalSheets];
}

/** @return array{0: float, 1: int} [total cost, total sheets] */
function print_cost(int $pages, int $pagesPerSheet, bool $color, bool $doubleside, int $copies,
                    string $size, bool $applyMinCost = true): array
{
    if ($pages < 1 || $copies < 1 || $copies > 999 || !in_array($pagesPerSheet, [1, 2, 4, 6, 9, 16], true)) {
        throw new HttpError(400, 'Invalid print options');
    }
    $colorRow = get_rates($size, true);
    $bwRow = get_rates($size, false);
    if (!$colorRow || !$bwRow) {
        throw new HttpError(400, "No pricing configured for paper_size=$size");
    }
    $colorCost = ['singleside' => (float) $colorRow['singleside_cost'], 'doubleside' => (float) $colorRow['doubleside_cost']];
    $bwCost = ['singleside' => (float) $bwRow['singleside_cost'], 'doubleside' => (float) $bwRow['doubleside_cost']];
    $minCost = (float) $bwRow['min_cost'];

    $odd = 0;
    $printPages = (int) ceil($pages / $pagesPerSheet);
    if ($doubleside) {
        $odd = $printPages % 2;
        $sheets = intdiv($printPages, 2);
    } else {
        $sheets = $printPages;
    }

    if ($color) {
        [$total, $totalSheets] = calc_cost($sheets, $doubleside, $copies, $odd, $colorCost);
    } else {
        [$cost, $totalSheets] = calc_cost($sheets, $doubleside, $copies, $odd, $bwCost);
        if (!$applyMinCost) {
            $total = $cost;
        } elseif ($printPages > 1 && $doubleside) {
            $total = $cost - $bwCost['doubleside'] + $minCost;
        } else {
            $total = $cost - $bwCost['singleside'] + $minCost;
        }
    }
    return [round($total, 2), $totalSheets];
}
