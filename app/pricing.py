"""
Print cost calculation.

This is calc_cost() and print_cost() from pdfcostcalc.py, unchanged in how
they calculate cost — same parameters, same control flow, same behavior
(including the original's quirk of only applying the minimum-charge floor
to B&W jobs, not color).

The only changes:
  - color_cost / bw_cost / min_cost are no longer hardcoded — they're
    fetched from the `pricing` table so the admin can edit them from the
    dashboard.
  - print_cost() takes an `apply_min_cost` flag. When False, the B&W
    minimum-charge substitution is skipped entirely and the job is charged
    at the plain per-sheet rate. Callers use this to waive the minimum
    charge for a phone number's 2nd+ job of the day (see main.py).
"""
import math

import fitz  # PyMuPDF

from . import db


def get_page_count(pdf_path):
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def calc_cost(sheets, doubleside, copies, Odd, color, costs):
    cost = 0
    if doubleside:
        cost = sheets * costs['doubleside'] + Odd * costs['singleside']
        Total_sheets = (sheets + Odd) * copies
    else:
        cost = sheets * costs['singleside']
        Total_sheets = (sheets + Odd) * copies

    Total_cost = cost * copies
    return Total_cost, Total_sheets


def print_cost(pages, pagespersheet, color, doubleside, copies, size, apply_min_cost=True):
    # --- rates now come from the DB instead of being hardcoded here ---
    color_row = db.get_rates(size, True)
    bw_row = db.get_rates(size, False)
    if not color_row or not bw_row:
        raise ValueError(f"No pricing configured for paper_size={size}")

    color_cost = {'singleside': color_row['singleside_cost'], 'doubleside': color_row['doubleside_cost']}
    bw_cost = {'singleside': bw_row['singleside_cost'], 'doubleside': bw_row['doubleside_cost']}
    # The original script kept one min_cost per paper size, and only ever
    # used it in the B&W branch below — so it's taken from the B&W row.
    min_cost = bw_row['min_cost']
    # --- everything below this line is unchanged from pdfcostcalc.py, apart
    # from the `apply_min_cost` gate around the floor substitution ---

    cost = 0
    Total_cost = 0
    Odd = 0
    sheets = 0
    printpages = math.ceil(pages / pagespersheet)
    if doubleside:
        Odd = printpages % 2
        sheets = int(printpages / 2)
    else:
        sheets = printpages

    if color:
        Total_cost, Total_sheets = calc_cost(sheets, doubleside, copies, Odd, color, color_cost)
    else:
        cost, Total_sheets = calc_cost(sheets, doubleside, copies, Odd, color, bw_cost)
        if not apply_min_cost:
            # 2nd+ job today for this phone number — charge the plain
            # per-sheet rate, no minimum-charge substitution.
            Total_cost = cost
        elif printpages > 1 and doubleside:
            Total_cost = cost - bw_cost['doubleside'] + min_cost
        else:
            Total_cost = cost - bw_cost['singleside'] + min_cost

    return Total_cost, Total_sheets
