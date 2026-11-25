# The expert panel

Run the draft past each lens that matters for the report. Each lens has: what it checks, the red flags
it raises, and what it must hand to a licensed professional. Write each lens's output as a short
section headed with the lens name, so the reader can see who said what.

---

## 1. Chartered Accountant / ACCA (finance, tax, bankability)

**Checks**
- Project cost: capital items (printers, repair benches, tools, computers, fit-out) vs pre-operative
  expenses vs working capital margin. Each line has a quote/invoice or is marked as an estimate.
- Means of finance: promoter contribution (margin), term loan, working capital, grants, equity.
  Promoter margin is commonly 10–25% depending on bank and scheme; state the lender's actual rule.
- Projections (from `scripts/projections.py`): revenue build-up from drivers (orders × ticket ×
  days), gross margin per line, fixed costs, depreciation, interest, tax, cash flow.
- Ratios a lender reads: DSCR (aim ≥ 1.25–1.5 on average, no year below ~1.2), current ratio
  (≥ 1.33 for working capital loans is a common benchmark), debt-equity, break-even occupancy/
  orders per day, payback, IRR/NPV if requested.
- Tax: GST registration (₹20 lakh threshold for services in Kerala, or from the first rupee in some
  cases), presumptive taxation (44AD/new section 58) vs regular, startup tax holiday (section 140 of the
  Income-tax Act 2025, formerly 80-IAC) — only with the Inter-Ministerial Board certificate.
- Accounting hygiene: separate business bank account, UPI collections reconciled to sales, GST
  invoices for online orders, depreciation rates.

**Red flags to raise**: revenue jumps with no driver; DSCR < 1.2 in any year; no working capital
for parts inventory (repairs); founder salaries missing (hides true cost); grant counted as revenue;
GST ignored on prices shown to customers.

**Must be signed by a practising CA**: projected financials for bank submission, CMA data, net-worth
certificates, tax positions (section 140 claim, 44AD), valuation reports for share issues.

---

## 2. Business lawyer (Indian business law)

**Checks**
- Entity choice: sole proprietorship (not DPIIT-eligible) vs partnership vs LLP vs private limited
  vs OPC. For investor funding or DPIIT benefits → Pvt Ltd (or LLP for DPIIT, but VCs prefer Pvt Ltd).
- Registrations: incorporation (MCA), PAN/TAN, GST, Kerala Shops & Commercial Establishments Act
  registration, trade licence from the local body (Kalamassery Municipality), Udyam (MSME), DPIIT
  recognition, Professional Tax (Kerala), EPF/ESI once headcount thresholds are reached.
- Online business: Information Technology Act 2000; Consumer Protection (E-Commerce) Rules 2020
  (seller details, grievance officer, refund/return policy on the site); Digital Personal Data
  Protection Act 2023 and its Rules (notice, consent, purpose limitation, retention, breach reporting)
  — print.tfix.in collects names, mobiles and files; repair.tfix.in collects device diagnostics.
- Repair business: written job card/terms (data loss disclaimer, parts warranty, unclaimed-device
  policy), E-Waste (Management) Rules 2022 for scrap parts/batteries.
- Printing: copyright — refuse wholesale photocopying of textbooks; a clear policy protects the shop.
- Contracts needed: founders' agreement (equity, vesting, IP assignment), employment/intern
  agreements with IP and confidentiality clauses, node/franchise agreement for partner shops
  (software licence, revenue share, brand use, quality standards, termination), vendor terms.

**Red flags**: no founders' agreement; IP (code, brand) owned by an individual not the company; no
privacy notice/consent; franchise model without a written agreement; operating without trade licence.

**Must be done/verified by an advocate or company secretary**: incorporation documents (MoA/AoA),
shareholder and founders' agreements, franchise/licence agreements, privacy policy and terms,
any notice or dispute.

---

## 3. IP specialist

**Checks**
- Trademarks: T FIX® is stated by the owner to be registered — record the registration number,
  class(es) and renewal date. Check coverage for new brands: CLUTCHPRINT, CLUTCH Rewards, Repair Lab,
  Phone Doctor — likely classes 40 (printing services), 37 (repair services), 41 (education/Academy),
  9 (software), 42 (SaaS/software services), 35 (retail/loyalty). Search the TM registry before use.
- Copyright: the software (T Print, CLUTCHPRINT, Phone Doctor) is protected automatically; consider
  registering key versions; make sure every contributor assigned rights to the company.
- Patents: software "per se" is not patentable in India (Patents Act section 3(k)); only consider if
  there is a genuine technical effect — usually not worth the cost at this stage.
- Designs: logo artwork can be protected via copyright/trademark; Designs Act is for product shapes.
- Open source: list licences of libraries used (pdf.js — Apache-2.0, Tailwind — MIT, etc.) and
  comply with notices.
- Costs: DPIIT-recognised startups get reduced statutory fees (verify current rebate; historically
  80% on patents, 50% on trademarks). The SIPP facilitator-fee scheme ended 31 March 2026 unless
  renewed — verify.

**Must be filed/advised by**: a registered trademark agent or patent attorney.

---

## 4. Branding strategist

**Checks**
- Architecture: master brand T FIX® ("Trouble & Tech") with sub-brands CLUTCHPRINT AI, Repair Lab,
  CLUTCH Rewards — is the hierarchy clear and consistent across sites, counter, social?
- Positioning per audience (students, parents, colleges, partner shops, investors), proof points
  (live sites, green counter, ratings), tone (confident, local, bilingual EN/ML).
- Brand rules: always "T FIX®" with ®; gold ▽ identity; see `print/BRAND.md` in the repo.
- Launch and growth: campus ambassadors, class-rep program (group orders), reels, standee QR,
  partner-node signage standards.

**Red flags**: claims that can't be proven ("AI" where it is rule-based — describe honestly);
inconsistent names; using ® on unregistered sub-brands (use ™ until registered).

---

## 5. Government-schemes advisor (ministries, state)

**Checks** (verify every item live — amounts and windows change)
- Eligibility gates: entity type, age, turnover, DPIIT recognition, Kerala registration, KSUM Unique ID,
  prior grants received (SISFS excludes startups that took more than ₹10 lakh from other schemes).
- Match the stage: idea/prototype → KSUM Idea Grant / SISFS grant; MVP/early revenue → KSUM
  Productisation/Market Acceleration; loans → MUDRA/CGTMSE/Credit Guarantee Scheme for Startups;
  procurement → GeM Startup Runway; marketing → KSUM marketing support; investors → KSUM Fund of Funds
  AIFs, Investor Café.
- For each scheme: amount, form (grant/debt/equity), documents, timeline, who decides, where to apply.

**Red flags**: applying before incorporation/DPIIT where required; double-dipping rules; claiming
"AI/deep tech" without substance (evaluators check).

---

## 6. Angel / VC investor

**Checks**
- Why now, why you, why big: market (sourced), wedge (campus), right to win (counter + software +
  ledger + local).
- Traction that matters: weekly active students, repeat rate, online share, average ticket, contribution
  margin per order, CAC (cost to win a student) and payback. No traction yet → say so and show the
  90-day plan to get it.
- Unit economics before scale; node economics (what a partner shop earns, what T FIX earns).
- Cap table and terms: founder equity, ESOP pool, instrument (SAFE-like iSAFE/CCD/CCPS), valuation
  logic (comparables, not hope). Angel tax was abolished from 1 April 2025 (verify); source-of-funds
  scrutiny remains.
- Risks and mitigations: execution, competition (local Xerox shops, Cashify/Instafix in repair),
  key-person, regulatory, seasonality (exam calendar).

**Red flags**: TAM with no source; "unicorn" talk without weekly numbers; valuation ask with no traction;
use of funds without milestones.
