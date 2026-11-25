#!/usr/bin/env python3
"""Project-report financial projections from a JSON inputs file.

Builds, for N years: revenue by line, variable costs, fixed costs, EBITDA, depreciation (written-down
value), interest, tax (with loss carry-forward), PAT; cash flow; a simplified balance sheet (checked
to balance); the term-loan schedule (moratorium + EMI); DSCR; break-even; payback; and a sensitivity
table. Every number comes from the inputs — nothing is assumed silently: missing optional values
default to zero and are listed under "Defaults used".

Usage:  python3 projections.py inputs.json --out OUTDIR
Writes: OUTDIR/projections.md, OUTDIR/projections.xlsx (or CSV files without openpyxl),
        OUTDIR/summary.json
"""
import argparse
import copy
import csv
import json
import math
import os
import sys


def inr(x, decimals=0):
    """Indian digit grouping: 12,34,567."""
    neg = x < 0
    x = abs(round(x, decimals))
    whole = int(x)
    frac = f"{x - whole:.{decimals}f}"[1:] if decimals else ""
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s + frac


def get(d, key, default, used, label):
    if key not in d or d[key] is None:
        used.append(f"{label} = {default}")
        return default
    return d[key]


def per_year(value, years):
    """A number applies to every year; a list gives year-by-year values (last one repeats)."""
    if isinstance(value, (int, float)):
        return [float(value)] * years
    vals = [float(v) for v in value] or [0.0]
    return [vals[min(i, len(vals) - 1)] for i in range(years)]


def loan_schedule(principal, rate_pct, tenure_years, moratorium_months, years):
    """Yearly interest, principal repaid and closing balance. Interest is paid during the moratorium."""
    r = rate_pct / 100 / 12
    total_m = int(round(tenure_years * 12))
    rep_m = max(total_m - moratorium_months, 0)
    if principal <= 0:
        return [0.0] * years, [0.0] * years, [0.0] * years, 0.0
    emi = principal / rep_m if r == 0 else principal * r / (1 - (1 + r) ** -rep_m) if rep_m else 0.0
    bal = principal
    interest, repaid, closing = [0.0] * years, [0.0] * years, [0.0] * years
    for m in range(years * 12):
        y = m // 12
        if bal <= 1e-6:
            closing[y] = 0.0
            continue
        i = bal * r
        if m < moratorium_months:
            p = 0.0
        else:
            p = min(emi - i, bal) if r else min(emi, bal)
        interest[y] += i
        repaid[y] += p
        bal -= p
        closing[y] = bal
    return interest, repaid, closing, emi


def build(inp, rev_mult=1.0, cost_mult=1.0):
    used = []
    years = int(get(inp, "years", 5, used, "years"))
    lines = inp.get("revenue_lines", [])
    fixed = inp.get("fixed_costs_per_year", {})
    fixed_growth = per_year(get(inp, "fixed_cost_growth_pct", 0, used, "fixed_cost_growth_pct"), years)

    # Revenue and variable cost by line
    rev_by_line, var_by_line = {}, {}
    for ln in lines:
        name = ln["name"]
        units = float(ln["units_per_day"])
        price = float(ln["price"])
        days = float(get(ln, "days_per_year", 330, used, f"{name}.days_per_year"))
        g_units = per_year(get(ln, "unit_growth_pct", 0, used, f"{name}.unit_growth_pct"), years)
        g_price = per_year(get(ln, "price_growth_pct", 0, used, f"{name}.price_growth_pct"), years)
        vpct = float(get(ln, "variable_cost_pct", 0, used, f"{name}.variable_cost_pct"))
        revs, vars_ = [], []
        for y in range(years):
            if y > 0:
                units *= 1 + g_units[y] / 100
                price *= 1 + g_price[y] / 100
            rv = units * price * days * rev_mult
            revs.append(rv)
            vars_.append(rv * vpct / 100 * cost_mult)
        rev_by_line[name], var_by_line[name] = revs, vars_

    revenue = [sum(rev_by_line[n][y] for n in rev_by_line) for y in range(years)]
    variable = [sum(var_by_line[n][y] for n in var_by_line) for y in range(years)]

    fixed_by_item = {}
    for item, amt in fixed.items():
        vals, a = [], float(amt)
        for y in range(years):
            if y > 0:
                a *= 1 + fixed_growth[y] / 100
            vals.append(a * cost_mult)
        fixed_by_item[item] = vals
    fixed_total = [sum(fixed_by_item[i][y] for i in fixed_by_item) for y in range(years)]

    preop = float(get(inp, "preoperative_expenses", 0, used, "preoperative_expenses"))

    # Fixed assets (written-down value depreciation, full year in year 1 unless half_year_first)
    capex = inp.get("capex", [])
    half = bool(get(inp, "half_year_depreciation_first_year", False, used, "half_year_depreciation_first_year"))
    dep = [0.0] * years
    nfa = [0.0] * years
    capex_total = 0.0
    for it in capex:
        cost = float(it["cost"])
        rate = float(get(it, "dep_rate_pct", 15, used, f"capex '{it.get('item', '?')}'.dep_rate_pct")) / 100
        yr = int(it.get("year", 1)) - 1
        capex_total += cost if yr == 0 else 0
        wdv = 0.0
        for y in range(years):
            if y == yr:
                wdv += cost
            d = wdv * rate * (0.5 if (half and y == yr) else 1.0)
            dep[y] += d
            wdv -= d
            nfa[y] += wdv
    capex_by_year = [sum(float(it["cost"]) for it in capex if int(it.get("year", 1)) - 1 == y) for y in range(years)]

    # Funding and loan
    f = inp.get("funding", {})
    promoter = float(get(f, "promoter", 0, used, "funding.promoter"))
    equity = float(get(f, "equity", 0, used, "funding.equity"))
    grant = float(get(f, "grant", 0, used, "funding.grant"))
    loan_amt = float(get(f, "term_loan", 0, used, "funding.term_loan"))
    ln_cfg = inp.get("loan", {})
    rate = float(get(ln_cfg, "rate_pct", 0, used, "loan.rate_pct"))
    tenure = float(get(ln_cfg, "tenure_years", 5, used, "loan.tenure_years"))
    morat = int(get(ln_cfg, "moratorium_months", 0, used, "loan.moratorium_months"))
    interest, principal, loan_bal, emi = loan_schedule(loan_amt, rate, tenure, morat, years)

    # Working capital = months of (variable + fixed cash costs)
    wc_months = float(get(inp.get("working_capital", {}), "months_of_costs", 0, used, "working_capital.months_of_costs"))
    nwc = [(variable[y] + fixed_total[y]) * wc_months / 12 for y in range(years)]

    tax_rate = float(get(inp.get("tax", {}), "rate_pct", 0, used, "tax.rate_pct")) / 100

    ebitda, pbt, tax, pat = [], [], [], []
    loss_cf = 0.0
    for y in range(years):
        e = revenue[y] - variable[y] - fixed_total[y] - (preop if y == 0 else 0)
        ebitda.append(e)
        p = e - dep[y] - interest[y]
        pbt.append(p)
        taxable = p
        if taxable > 0 and loss_cf > 0:
            off = min(loss_cf, taxable)
            taxable -= off
            loss_cf -= off
        elif taxable < 0:
            loss_cf += -taxable
            taxable = 0
        t = taxable * tax_rate if taxable > 0 else 0.0
        tax.append(t)
        pat.append(p - t)

    # Cash flow and balance sheet
    inflow0 = promoter + equity + grant + loan_amt
    cash, cash_open, cfo, cf_rows = 0.0, [], [], []
    re_cum = 0.0
    bs = []
    for y in range(years):
        opening = cash
        d_nwc = nwc[y] - (nwc[y - 1] if y else 0.0)
        inflow = inflow0 if y == 0 else 0.0
        op = pat[y] + dep[y] - d_nwc
        cash = opening + inflow + op - capex_by_year[y] - principal[y]
        cash_open.append(opening)
        cfo.append(op)
        cf_rows.append({"opening": opening, "funding_in": inflow, "pat": pat[y], "depreciation": dep[y],
                        "increase_in_working_capital": d_nwc, "capex": capex_by_year[y],
                        "loan_repaid": principal[y], "closing": cash})
        re_cum += pat[y]
        assets = cash + nfa[y] + nwc[y]
        liab = promoter + equity + grant + re_cum + loan_bal[y]
        bs.append({"net_fixed_assets": nfa[y], "net_working_capital": nwc[y], "cash": cash, "total_assets": assets,
                   "promoter_capital": promoter, "investor_equity": equity, "grant_capital_reserve": grant,
                   "retained_earnings": re_cum, "term_loan": loan_bal[y], "total_liabilities": liab,
                   "balances": abs(assets - liab) < 1})

    dscr = []
    for y in range(years):
        ds = interest[y] + principal[y]
        dscr.append((pat[y] + dep[y] + interest[y]) / ds if ds > 0 else None)
    dvals = [d for d in dscr if d is not None]

    contrib_ratio = [(revenue[y] - variable[y]) / revenue[y] if revenue[y] else 0 for y in range(years)]
    be_rev = [((fixed_total[y] + dep[y] + interest[y] + (preop if y == 0 else 0)) / contrib_ratio[y]) if contrib_ratio[y] > 0 else None
              for y in range(years)]

    invest = capex_total + preop + (nwc[0] if years else 0)
    cum, payback = 0.0, None
    for y in range(years):
        prev = cum
        cum += cfo[y]
        if payback is None and cum >= invest and cfo[y] > 0:
            payback = y + (invest - prev) / cfo[y]

    return {
        "years": years, "labels": [f"Y{y + 1}" for y in range(years)], "start_year": inp.get("start_year", ""),
        "rev_by_line": rev_by_line, "var_by_line": var_by_line, "revenue": revenue, "variable": variable,
        "fixed_by_item": fixed_by_item, "fixed_total": fixed_total, "preop": preop, "ebitda": ebitda,
        "depreciation": dep, "interest": interest, "pbt": pbt, "tax": tax, "pat": pat,
        "cash_flow": cf_rows, "balance_sheet": bs, "loan": {"amount": loan_amt, "rate_pct": rate, "tenure_years": tenure,
        "moratorium_months": morat, "emi": emi, "interest": interest, "principal": principal, "closing": loan_bal},
        "dscr": dscr, "dscr_avg": sum(dvals) / len(dvals) if dvals else None, "dscr_min": min(dvals) if dvals else None,
        "break_even_revenue": be_rev, "break_even_pct": [be_rev[y] / revenue[y] * 100 if be_rev[y] and revenue[y] else None for y in range(years)],
        "initial_investment": invest, "funding_total": inflow0, "payback_years": payback,
        "funding": {"promoter": promoter, "equity": equity, "grant": grant, "term_loan": loan_amt},
        "defaults_used": used,
    }


def fmt(v):
    return "—" if v is None else inr(v)


def table(title_col, labels, rows):
    out = ["| " + title_col + " | " + " | ".join(labels) + " |", "|---|" + "---:|" * len(labels)]
    for name, vals, bold in rows:
        cells = [fmt(v) for v in vals]
        nm = f"**{name}**" if bold else name
        if bold:
            cells = [f"**{c}**" for c in cells]
        out.append(f"| {nm} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def markdown(m, inp, sens):
    L = m["labels"]
    s = [f"# Financial projections — {inp.get('company', '')}", "",
         f"All amounts in ₹. Years: {', '.join(L)}" + (f" (Y1 = {m['start_year']})" if m["start_year"] else "") + ".",
         "Projections are estimates built only from the inputs file; they are not audited or certified.", ""]
    f = m["funding"]
    s += ["## Project cost and means of finance", "",
          "| Item | ₹ |", "|---|---:|",
          f"| Capital expenditure (Y1) | {inr(m['initial_investment'] - m['preop'] - (m['balance_sheet'][0]['net_working_capital'] if m['years'] else 0))} |",
          f"| Pre-operative expenses | {inr(m['preop'])} |",
          f"| Working capital (Y1 requirement) | {inr(m['balance_sheet'][0]['net_working_capital'] if m['years'] else 0)} |",
          f"| **Total project cost** | **{inr(m['initial_investment'])}** |",
          f"| Promoter contribution | {inr(f['promoter'])} |", f"| Investor equity | {inr(f['equity'])} |",
          f"| Grant | {inr(f['grant'])} |", f"| Term loan | {inr(f['term_loan'])} |",
          f"| **Total funding** | **{inr(m['funding_total'])}** |", ""]
    if abs(m["funding_total"] - m["initial_investment"]) > 1:
        gap = m["funding_total"] - m["initial_investment"]
        s += [f"> Funding {'exceeds' if gap > 0 else 'falls short of'} project cost by ₹{inr(abs(gap))}.", ""]
    rows = [(f"Revenue — {n}", v, False) for n, v in m["rev_by_line"].items()]
    rows += [("Total revenue", m["revenue"], True), ("Variable costs", m["variable"], False)]
    rows += [(f"Fixed — {n}", v, False) for n, v in m["fixed_by_item"].items()]
    rows += [("Pre-operative (Y1)", [m["preop"]] + [0] * (m["years"] - 1), False),
             ("EBITDA", m["ebitda"], True), ("Depreciation", m["depreciation"], False), ("Interest", m["interest"], False),
             ("Profit before tax", m["pbt"], True), ("Tax", m["tax"], False), ("Profit after tax", m["pat"], True)]
    s += ["## Projected profit and loss", "", table("₹", L, rows), ""]
    cf = m["cash_flow"]
    s += ["## Projected cash flow", "", table("₹", L, [
        ("Opening cash", [r["opening"] for r in cf], False), ("Funding received", [r["funding_in"] for r in cf], False),
        ("Profit after tax", [r["pat"] for r in cf], False), ("Add: depreciation", [r["depreciation"] for r in cf], False),
        ("Less: increase in working capital", [r["increase_in_working_capital"] for r in cf], False),
        ("Less: capital expenditure", [r["capex"] for r in cf], False), ("Less: loan principal repaid", [r["loan_repaid"] for r in cf], False),
        ("Closing cash", [r["closing"] for r in cf], True)]), ""]
    bs = m["balance_sheet"]
    s += ["## Projected balance sheet (simplified)", "", table("₹", L, [
        ("Net fixed assets", [b["net_fixed_assets"] for b in bs], False), ("Net working capital", [b["net_working_capital"] for b in bs], False),
        ("Cash", [b["cash"] for b in bs], False), ("Total assets", [b["total_assets"] for b in bs], True),
        ("Promoter capital", [b["promoter_capital"] for b in bs], False), ("Investor equity", [b["investor_equity"] for b in bs], False),
        ("Grant (capital reserve)", [b["grant_capital_reserve"] for b in bs], False), ("Retained earnings", [b["retained_earnings"] for b in bs], False),
        ("Term loan outstanding", [b["term_loan"] for b in bs], False), ("Total liabilities & capital", [b["total_liabilities"] for b in bs], True)]),
        "", "Balance check: " + ("all years balance ✓" if all(b["balances"] for b in bs) else "⚠ DOES NOT BALANCE — check inputs"), ""]
    low = min((r["closing"], i) for i, r in enumerate(cf)) if cf else (0, 0)
    if low[0] < 0:
        s += [f"> ⚠ **Cash goes negative** (lowest ₹{inr(low[0])} in {L[low[1]]}). The plan needs about ₹{inr(-low[0])} more "
              "funding or working-capital credit, lower costs, or faster revenue before it is bankable.", ""]
    ln = m["loan"]
    if ln["amount"] > 0:
        s += ["## Term loan schedule", "",
              f"Loan ₹{inr(ln['amount'])} at {ln['rate_pct']}% for {ln['tenure_years']} years incl. {ln['moratorium_months']}-month moratorium "
              f"(interest paid during moratorium). EMI after moratorium: ₹{inr(ln['emi'])}.", "",
              table("₹", L, [("Interest", ln["interest"], False), ("Principal repaid", ln["principal"], False), ("Closing balance", ln["closing"], True)]), ""]
        d = [None if x is None else round(x, 2) for x in m["dscr"]]
        s += ["## Debt service coverage ratio (DSCR)", "",
              "DSCR = (profit after tax + depreciation + interest) ÷ (interest + principal). Lenders commonly look for ≥ 1.25–1.5 on average.", "",
              "| | " + " | ".join(L) + " |", "|---|" + "---:|" * len(L),
              "| DSCR | " + " | ".join("—" if x is None else f"{x:.2f}" for x in d) + " |", "",
              f"Average DSCR: **{m['dscr_avg']:.2f}** · Minimum: **{m['dscr_min']:.2f}**" if m["dscr_avg"] is not None else "", ""]
    s += ["## Break-even", "", "Break-even revenue = fixed costs (incl. depreciation and interest) ÷ contribution margin.", "",
          "| | " + " | ".join(L) + " |", "|---|" + "---:|" * len(L),
          "| Break-even revenue (₹) | " + " | ".join(fmt(x) for x in m["break_even_revenue"]) + " |",
          "| As % of projected revenue | " + " | ".join("—" if x is None else f"{x:.0f}%" for x in m["break_even_pct"]) + " |", ""]
    s += [f"Payback of initial investment (₹{inr(m['initial_investment'])}): " +
          (f"**{m['payback_years']:.1f} years**" if m["payback_years"] else f"not within {m['years']} years"), ""]
    s += ["## Sensitivity", "", "| Case | Y2 profit after tax | Average DSCR | Minimum DSCR |", "|---|---:|---:|---:|"]
    for name, sm in sens:
        s.append(f"| {name} | {inr(sm['pat'][1] if sm['years'] > 1 else sm['pat'][0])} | "
                 f"{'—' if sm['dscr_avg'] is None else f'{sm['dscr_avg']:.2f}'} | {'—' if sm['dscr_min'] is None else f'{sm['dscr_min']:.2f}'} |")
    s += ["", "## Defaults used (inputs that were missing)", ""]
    s += [f"- {u}" for u in m["defaults_used"]] or ["- none"]
    return "\n".join(s) + "\n"


def write_xlsx(m, path):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "P&L"
    L = m["labels"]

    def sheet(ws, rows):
        ws.append(["₹"] + L)
        ws["A1"].font = Font(bold=True)
        for name, vals in rows:
            ws.append([name] + [None if v is None else round(v, 2) for v in vals])
        ws.column_dimensions["A"].width = 38
        for col in range(2, len(L) + 2):
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 16
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=col).number_format = "#,##0"

    rows = [(f"Revenue — {n}", v) for n, v in m["rev_by_line"].items()] + [("Total revenue", m["revenue"]), ("Variable costs", m["variable"])]
    rows += [(f"Fixed — {n}", v) for n, v in m["fixed_by_item"].items()]
    rows += [("Pre-operative (Y1)", [m["preop"]] + [0] * (m["years"] - 1)), ("EBITDA", m["ebitda"]), ("Depreciation", m["depreciation"]),
             ("Interest", m["interest"]), ("Profit before tax", m["pbt"]), ("Tax", m["tax"]), ("Profit after tax", m["pat"])]
    sheet(ws, rows)
    cf = m["cash_flow"]
    sheet(wb.create_sheet("Cash flow"), [(k.replace("_", " ").capitalize(), [r[k] for r in cf]) for k in cf[0]])
    bs = m["balance_sheet"]
    sheet(wb.create_sheet("Balance sheet"), [(k.replace("_", " ").capitalize(), [b[k] for b in bs]) for k in bs[0] if k != "balances"])
    ln = m["loan"]
    sheet(wb.create_sheet("Loan & DSCR"), [("Interest", ln["interest"]), ("Principal repaid", ln["principal"]),
                                           ("Closing balance", ln["closing"]), ("DSCR", m["dscr"])])
    sheet(wb.create_sheet("Break-even"), [("Break-even revenue", m["break_even_revenue"]), ("Break-even % of revenue", m["break_even_pct"])])
    wb.save(path)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    with open(a.inputs, encoding="utf-8") as fh:
        inp = json.load(fh)
    if not inp.get("revenue_lines"):
        sys.exit("inputs: 'revenue_lines' is required (at least one line with name, units_per_day, price)")
    os.makedirs(a.out, exist_ok=True)
    m = build(inp)
    sens = [("Base case", m), ("Revenue −10%", build(inp, rev_mult=0.9)), ("Revenue −20%", build(inp, rev_mult=0.8)),
            ("Costs +10%", build(inp, cost_mult=1.1))]
    with open(os.path.join(a.out, "projections.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown(m, inp, sens))
    xlsx = write_xlsx(m, os.path.join(a.out, "projections.xlsx"))
    if not xlsx:
        with open(os.path.join(a.out, "projections_pl.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["₹"] + m["labels"])
            for k in ("revenue", "variable", "fixed_total", "ebitda", "depreciation", "interest", "pbt", "tax", "pat"):
                w.writerow([k] + [round(v, 2) for v in m[k]])
    summary = {k: m[k] for k in ("labels", "revenue", "ebitda", "pat", "dscr", "dscr_avg", "dscr_min", "break_even_revenue",
                                 "initial_investment", "funding_total", "payback_years", "defaults_used")}
    summary["balance_sheet_balances"] = all(b["balances"] for b in m["balance_sheet"])
    summary["lowest_cash"] = min(r["closing"] for r in m["cash_flow"]) if m["cash_flow"] else 0
    summary["sensitivity"] = {n: {"y2_pat": (s["pat"][1] if s["years"] > 1 else s["pat"][0]), "dscr_min": s["dscr_min"]} for n, s in sens}
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"Wrote {a.out}/projections.md, " + ("projections.xlsx" if xlsx else "projections_pl.csv") + ", summary.json")
    print(f"DSCR avg {summary['dscr_avg'] and round(summary['dscr_avg'], 2)} · min {summary['dscr_min'] and round(summary['dscr_min'], 2)} · "
          f"balance sheet {'balances' if summary['balance_sheet_balances'] else 'DOES NOT BALANCE'}")


if __name__ == "__main__":
    main()
