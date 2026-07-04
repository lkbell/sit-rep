# Sit-Rep — Maintainer Runbook

For the Claude session (any model) maintaining this project for Landon Bell. Read this before touching anything. The cloud brain's 🗂️ Projects page "Sit-Rep — Ground-Truth Dashboards" holds history and rationale; THIS file is the operating manual.

## What this is

Live site: **https://lkbell.github.io/sit-rep/** — ~69 indicator charts, 10 domains + an Overview page of 16 vital signs. Purpose: Landon's ground-truth situational awareness. Data auto-refreshes via GitHub Actions; the site is pure static files on GitHub Pages.

## Architecture (5 files matter)

- `pipeline/fetch.py` — everything data: the CATALOG dict (one entry per chart: source, transform, unit, freshness window `exp`, note) + per-source adapters + transforms. Emits `data/<id>.json` per chart, `data/catalog.json` (drives the site), `data/manifest.json` (per-chart status/freshness).
- `app.js` — renders catalog+data with uPlot; hash routing (`#/` Overview, `#/<section>`); freshness dots; explainers.
- `explainers.json` — static "What it measures / Why it matters / What noteworthy looks like" text per chart id (currently the 16 Overview charts). The ⓘ button appears automatically for any chart id present here.
- `style.css`, `index.html` — presentation shell.
- `.github/workflows/refresh.yml` — cron + on-push refresh; commits refreshed `data/` back to main.

## Golden rules (do not regress these)

1. **Wrong numbers are the cardinal sin.** Transforms are calendar-aware (`t_yoy` compares same-month-prior-year and SKIPS gap months — e.g. the Oct-2025 CPI shutdown gap; `t_rollsum` requires all 12 calendar months). Never replace with naive index-offset math.
2. **Failures stay isolated and loud.** One bad feed = one red dot + header badge, last-good data retained, everything else refreshes. Never let one adapter exception kill the run; never hide a failure.
3. **The site is data-only.** No AI commentary, no adjectives in notes. Explainers are static, historically-anchored, and politically even-handed — run the reversal test ("would I write the same with the sides swapped?") on any edit. Interpretation lives only in the Sunday digest (a Claude scheduled task on Landon's machine), clearly labeled as Claude's Take.
4. **Keyless sources only.** No API keys in this repo, ever.

## Routine operations

**Add an indicator:** one CATALOG entry in `pipeline/fetch.py` (+ a JSON in `pipeline/manual/` if curated; + an `explainers.json` entry if it should have an ⓘ). Push to main → workflow re-runs → site picks it up. Nothing else to touch.

**Fix a broken feed:** `data/manifest.json` tells you what and why — adapters raise descriptive errors, and the GSCPI adapter dumps the file's actual sheet/row layout into its error message. Fix the one adapter, push, watch the next run. Known source quirks:
- FRED (`fredgraph.csv`, no key): the workhorse. Missing values are `.`; header row varies.
- Yahoo Finance (S&P/gold/SOX): most fragile; FRED (`SP500`) and Stooq fallbacks are wired via each entry's `fallback` key. FRED's SP500 only has ~10y history — if the fallback engages, the chart shortens.
- NY Fed GSCPI: legacy .xls served under an .xlsx name; branded cover sheet; data on "GSCPI Monthly Data"; TEXT dates like `31-Jan-1998`. `_cell_date()` handles xldate floats + 4 text formats; extend it there if a new format appears.
- EIA crude (.xls via xlrd), RTCI (CSV schema may drift — uses `Date_Through`, `crime_type`, `Percent_Change`), Zillow (file URLs occasionally renamed), OWID datacenter (grapher CSV), GPR (xls, `GPR` column).

**Curated annual series** (in `pipeline/manual/`, values must be verified against the actual release before updating): FBI murder rate (fall) · CDC fertility / life expectancy / marriage (spring–summer) · Gallup trust in media (Sept–Oct) · CBP border FY totals (Oct–Nov). Their dots age to amber automatically if an update is missed (per-chart `exp`).

**Verification checklist after ANY push:** (1) Actions run went green and a `data refresh` commit by groundtruth-bot appeared; (2) `data/manifest.json` shows 0 failed (or only known/expected ones); (3) hard-refresh the live site — charts render, no console errors, badge count correct.

## Working through the GitHub connector (hard limits)

The connector CANNOT: create repos, push `.github/workflows/*` files, or reach repos not granted in Landon's app installation. Landon does those by hand. File updates require the FULL file content + current blob SHA (`create_or_update_file`) — for big files (fetch.py ~45KB), the proven pattern is a **surgical single-function replacement**: fetch current content, swap exactly one delimited function, sanity-check landmarks (`def src_owid`, `CATALOG = {`, one occurrence of the edited def, ends with `main()`), push with the SHA, then verify per the checklist. Never push a partial file.

## Standing backlog (rough priority)

In-site signals/alerting layer (threshold strip on Overview) · explainers for the remaining ~53 domain-page charts (match the existing 16 in tone: ≤3 short fields, historical anchors, no adjectives) · presidential approval + generic ballot feed (no reliable keyless source found as of Jul 2026 — don't ship shaky numbers) · election odds (Polymarket API) · monthly border-encounters feed (OHSS/CBP portal is JS-rendered; needs browser work) · affordability ratio · WSTS semiconductor sales · AI-scatter hover labels (model names already ship in `ai_compute.json`) · living religiosity series · percentile-vs-history markers · "changed since last visit" view · mobile/PWA polish.

Built 2026-07-03 by Claude (Fable) for Landon Bell; maintained by whichever Claude is on duty.
