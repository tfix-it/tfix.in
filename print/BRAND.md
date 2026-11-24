# CLUTCHPRINT AI — T FIX® CUSAT brand brief (print.tfix.in)

Brand context for every build step on print.tfix.in (Antigravity / Codex / Claude Code).
Source: owner's finalized brief. Keep wording exactly as written unless the owner changes it.

## Brand & place
- **Brand:** T FIX® CUSAT — T FIX is a registered trademark: **always write T FIX®** (with ®).
- **"T"** stands for **Trouble and Tech**.
- **Address:** South Pipeline Road, CUSAT PO, Kalamassery 682022 — "Pipeline Road, Kalamassery".
- **Hours:** open daily till 9:30 PM (Mon–Sat 8:30 AM–9:30 PM, Sun 9:30 AM–9:30 PM).
- **URL:** print.tfix.in · main site tfix.in

## Product name — CLUTCHPRINT
- Forward-slanted **bold gold "CLUTCH"** + **thin white "PRINT"**.
- A small, forward-slanted, **gold-bordered "AI" capsule** at the upper-right of the "T".
- In code: `.clutch-wordmark` / `.clutch-part` / `.print-part` / `.ai-capsule` in `index.html`.

## Palette & type
- Deep matte charcoal radial background (`#07080a` → `#181a20`) with a gold lattice of inverted triangles (the ▽
  emblem) that drifts on scroll, plus slowly turning concentric ▽ rings (calm/static with reduced motion).
- Metallic T FIX gold matching the inverted-triangle T FIX® CUSAT logo:
  `#fff0b3` / `#f3d97d` / `#d4af37` / `#9c7518` (logo gradient `#FCE379 → #F5D466 → #DEB23A`).
- Crisp white body text; zinc greys for secondary text.
- Fonts: Inter (text), JetBrains Mono (monospace accents: page counts, prices, pickup tokens),
  Noto Sans Malayalam (Malayalam).
- Logo: vector rebuild of the owner's reference (`tfix-logo.svg`, `favicon.svg`).

## Copy (exact)
- **Tagline:** No queue. No panic. Upload anytime — grab your prints in seconds.
- **Status pill:** ● CLUTCHPRINT LIVE · OPEN DAILY TILL 9:30 PM
  *(when the shop is closed the pill says so and when it opens — live, India time)*
- **Hero headline:** Last-minute print? You're covered.
- **Hero subtext:** Upload from your hostel, class, or bus. Our AI handles the queue—just walk into T FIX® CUSAT and grab your pages.
- **Primary CTA:** Drop Your File (PDF / DOCX / JPG)
- **Live AI status steps:** Reading pages… → Setting layout… → Locked in. Ready at T FIX.
- **Pickup card line:** Show token #CP-104 at the T FIX counter—zero waiting.
- **3-step flow:** 1. UPLOAD · 2. AUTO-PRINT · 3. GRAB & GO

## Pickup tokens
- Every online order gets a token **#CP-<number>** (CP-101, CP-102, …) — issued by
  `api/order.php`, shown on the pickup pass and in T Print admin as `[CP-105 · Name] file.pdf`.
- Tracking an order needs the customer's private link (code + secret, saved on the phone that ordered), so numbers can't be guessed to see other orders.

## CLUTCH Rewards (online customers only)
- **Mobile number is mandatory** on every online order and is the customer's **rewards ID**. No PIN, no password.
- **Every ₹100 of prints earns ₹5** (counted on the customer's total online spend, so ₹60 + ₹40 also earns ₹5).
- Rewards are added when the order is **collected** (paid at the counter or by UPI), using T Print's final price.
- The pickup pass shows the balance, a progress bar to the next ₹5, and what this order added.
- Using rewards: at the T FIX® counter. Staff check and use them in T Print admin (CLUTCH Rewards panel),
  only for the customer collecting an online order on that number.
- In-shop (walk-in) T Print jobs don't earn rewards and don't use #CP tokens.
- **Tamper-evident log:** every credit and use is written to `reward_ledger`, each entry sealed with the
  SHA-256 of the one before it (a hash chain, no cryptocurrency). The customer's pass shows their history with
  short receipt codes; T Print admin shows "Rewards log verified" or what was changed. A future on-chain
  token can be built from this log without losing history.

## Search appearance
- Google site name: **ClutchPrint AI** (WebSite structured data + og:site_name in `index.html`), page title
  "CLUTCHPRINT AI — T FIX® CUSAT | print.tfix.in", 192 px icon for search results.

## Languages
- English and Malayalam (toggle in the header). All text lives in the `T` dictionary in `index.html`.
- Brand words stay in English in both: T FIX®, CUSAT, CLUTCHPRINT, CLUTCH Rewards, AI, #CP tokens, UPI.

## Launch assets already prepared (by the owner)
- 16:9 dark-and-gold banner
- 9:16 vertical counter QR standee (1. UPLOAD · 2. AUTO-PRINT · 3. GRAB & GO)
- 1:1 Instagram/WhatsApp launch post
- 15-second AI video prompt + Manglish Reel voiceover for CUSAT students

## How it really works (keep claims true)
- Upload any time; orders wait online while the shop is closed.
- T Print on Shop PC 1 fetches orders, counts pages, prices them at the shop's rates, queues them.
- Customer pays by UPI (from the pickup pass) or at the counter; printing starts after staff confirm payment.
- The page shows live shop hours and whether the print server (Shop PC 1) is online.
