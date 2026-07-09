# Sit-Rep — Maintainer Runbook

For the Claude session (any model) maintaining this project for Landon Bell. Read this before touching anything. The cloud brain's 🗂️ Projects page "Sit-Rep — Ground-Truth Dashboards" holds history and rationale; THIS file is the operating manual.

## What this is

Live site: **https://lkbell.github.io/sit-rep/** — 75 indicator charts, 10 domains + an Overview page of 16 vital signs with a signals strip. Purpose: Landon's ground-truth situational awareness. Data auto-refreshes via GitHub Actions (weekdays 14:23 UTC + on any pipeline push); the site is pure static files on GitHub Pages.

## Architecture (6 files matter)

- `pipeline/fetch.py` — everything data: the CATALOG dict (one entry per chart: source, transform, unit, freshness window `exp`, note) + per-source adapters + transforms + the SIGNALS rules (`compute_signals`). Emits `data/<id>.json` per chart, `data/catalog.json` (drives the site), `data/manifest.json` (per-chart status/freshness), `data/signals.json` (early-warning rules).
- `app.js` — renders catalog+data with uPlot; hash routing (`#/` Overview, `#/<section>`); freshness dots; explainer toggles; the Overview signals bar. Hover tooltip: `tooltipPlugin` (in app.js) is added to every chart's uPlot `opts.plugins` in `redraw()` — on hover it shows the date + value(s) at the cursor point, with a highlighted point and a vertical crosshair (`cursor.y` off); multi-series charts show one colored row per series. Styled by the `.u-tt*` rules in style.css. Bar charts (crime) are separate DOM (`renderBars`) and already print their values.
- `explainers.json` — static "What it measures / Why it matters / What noteworthy looks like" text, ALL 75 chart ids. The ⓘ button appears automatically for any chart id present here.
- `style.css`, `index.html` — presentation shell (the SITREP wordmark lives here: `h1.logo`, red #d7261e / dark #ff4136).
- `.github/workflows/refresh.yml` — cron + on-push refresh; commits refreshed `data/` back to main.

## The signals layer (added 2026-07-06; Landon may turn it off)

Seven calibrated early-warning rules computed by `compute_signals()` in fetch.py after each refresh: Sahm ≥ 0.50 · yield-curve zero cross within 60 days · HY OAS > 5pp · claims +15% off 26-wk low · NFCI > 0 · CPI ≥4% or ≤0% · GPR ≥ 2x 5-yr avg. Output: `data/signals.json`. The site shows a collapsed one-line bar on the Overview (red-tinted when tripped, click to expand, × hides permanently via localStorage `gt-signals`).
**Kill switches:** (1) global — set `SIGNALS_ENABLED = False` at the top of fetch.py and push (bar disappears for everyone); (2) per-browser — the × on the bar; restore by clearing the `gt-signals` localStorage key. **Tuning thresholds:** each rule's threshold is anchored to the historical record — do not loosen them casually; a strip that cries wolf gets ignored (dot-blindness). Change one rule at a time and note why in the commit message.

## Routine operations

**Add an indicator:** one CATALOG entry in `pipeline/fetch.py` (+ a JSON in `pipeline/manual/` if curated; + an `explainers.json` entry — match the house style: three short fields, historical anchors instead of adjectives, reversal-tested). Push to main → workflow re-runs → site picks it up.

**Fix a broken feed:** `data/manifest.json` tells you what and why — adapters raise descriptive errors, and the GSCPI adapter dumps the file's actual sheet/row layout into its error message. Fix the one adapter, push, watch the next run. Known source quirks:
- FRED (`fredgraph.csv`, no key): the workhorse. Missing values are `.`; header row varies.
- Yahoo Finance (S&P/gold/SOX): most fragile; FRED (`SP500`) and Stooq fallbacks are wired via each entry's `fallback` key. FRED's SP500 only has ~10y history — if the fallback engages, the chart shortens.
- NY Fed GSCPI: legacy .xls served under an .xlsx name; branded cover sheet; data on "GSCPI Monthly Data"; TEXT dates like `31-Jan-1998`. `_cell_date()` handles xldate floats + 4 text formats; extend it there if a new format appears.
- **VoteHub approval** (`src_votehub_approval`): keyless JSON at api.votehub.com/polls?poll_type=approval. We compute an unweighted 14-day trailing mean of all 'Donald Trump' subject polls, stepped weekly. If the API dies, candidate replacements: Datawrapper datasets behind public trackers, or Wikipedia approval tables. Subject string must change with any new president.
- **CBP monthly border encounters** (`src_cbp_monthly` / `border_monthly`, added 2026-07-08): SELF-DISCOVERING — scrapes the CBP Public Data Portal page `document/stats/nationwide-encounters` for the "Nationwide Encounters by Area of Responsibility" (`-aor`) CSVs, keeps the newest file per fiscal-year range (rolling `fy23-fy26-<mon>` file + the FY20/FY21/FY22 archives), merges them (newer revisions win) and sums Encounter Count to monthly totals back to Oct 2019. Two series: Nationwide (all rows) and Southwest land border (the "Southwest Land Border" region rows). Parse quirks: the current FY is labeled e.g. `2026 (FYTD)`; fiscal Oct–Dec map to the prior calendar year; parse with `csv.reader` (the Citizenship column contains quoted commas). The archive files sit under a DIFFERENT path shape (`/files/assets/documents/<folder>/…`) than the rolling file (`/files/<folder>/…`), so the per-range folder is taken as the last path segment before the filename — miss this and the FY20 archive is silently dropped (series starts FY2021 instead of FY2020). Validation: the sums reproduce CBP’s published figures exactly — nationwide FY2023 = 3,201,144 (and the independent `-aor`/`-state` breakdowns agree to the unit); the Southwest cut equals the standalone SW product (FY2023 2,475,669, FY2024 2,135,005). If CBP renames the `-aor` files or drops the region column, the adapter raises with the header it saw.
- **Polymarket midterms** (`src_polymarket_dem`): SELF-DISCOVERING — finds the "Which party will win the House/Senate in 2026" event via gamma-api public-search, picks the Democratic Party market, pulls daily history from clob.polymarket.com/prices-history. After the Nov 2026 election it will resolve and eventually 404 — replace the search string with the next cycle's race (e.g. 2028 president) or retire the chart.
- **Treasury Debt-to-the-Penny** (`src_treasury_debt` / `debt`): INTERMITTENT — the FiscalData API is occasionally very slow and a single page can exceed the read timeout, failing the feed red for that refresh (last-good data retained, so the chart still shows through its prior date). Paginated at 1,000 rows/page with a 3-try retry + 90s timeout (2026-07-09), which reduced but did NOT eliminate it — it failed again on the 2026-07-09 02:16 UTC run. **Landon's call (2026-07-09): leave it as-is** — it self-heals whenever Treasury's API is responsive; do not treat an occasional red `debt` card as a regression. If it becomes chronic, the levers are smaller pages, a longer per-page timeout, or more retries.
- EIA crude (.xls via xlrd), RTCI (CSV schema may drift — uses `Date_Through`, `crime_type`, `Percent_Change`), Zillow (file URLs occasionally renamed), OWID datacenter (grapher CSV), GPR (xls, `GPR` column).

**Curated annual series** (in `pipeline/manual/`, values must be verified against the actual release before updating): FBI murder rate (fall) · CDC fertility / life expectancy / marriage (spring–summer) · Gallup trust in media (Sept–Oct) · CBP border FY totals (Oct–Nov). Their dots age to amber automatically if an update is missed (per-chart `exp`).

**Verification checklist after ANY push:** (1) Actions run went green and a `data refresh` commit by groundtruth-bot appeared; (2) `data/manifest.json` shows 0 failed; (3) hard-refresh the live site — charts render, no console errors, badge count correct, signals bar behaves.

## Golden rules (do not regress these)

1. **Wrong numbers are the cardinal sin.** Transforms are calendar-aware (`t_yoy` compares same-month-prior-year and SKIPS gap months — e.g. the Oct-2025 CPI shutdown gap; `t_rollsum` requires all 12 calendar months). Never replace with naive index-offset math. Ship only feeds you have verified end-to-end against the actual source.
2. **Failures stay isolated and loud.** One bad feed = one red dot + header badge, last-good data retained. Never let one adapter exception kill the run; never hide a failure.
3. **The site is data-only.** No AI commentary; notes and explainers are static, historically-anchored, and politically even-handed — run the reversal test ("would I write the same with the sides swapped?") on any edit. Live interpretation exists ONLY in the Sunday digest (a Claude scheduled task on Landon's machine, leads with a clearly-labeled "Claude's Take").
4. **Keyless sources only.** No API keys in this repo, ever.

## Working through the GitHub connector (hard limits)

The connector CANNOT: create repos, push `.github/workflows/*` files, or reach repos not granted in Landon's app installation. Landon does those by hand. File updates require the FULL file content (`push_files`, or `create_or_update_file` + current blob SHA) — for big files (fetch.py ~53KB), the proven pattern is a **surgical single-function replacement**: fetch current content, swap exactly one delimited function, sanity-check landmarks (one `def main():`, one `CATALOG = {`, `SIGNALS_ENABLED` present, ends with `main()`), py_compile, push, then verify per the checklist. Never push a partial file. When a file is too large to round-trip through the connector at all (fetch.py is now ~59KB and a full read can exceed the connector's return limit), edit it in **GitHub's web editor** instead: with Landon's browser logged into GitHub, open the file's `/edit/main/<path>` URL, grab the CodeMirror-6 EditorView (scan the `.cm-*` nodes for the object whose `.state.doc` and `.dispatch` exist), read the full doc, apply anchored string edits, and hash/JSON-validate the result in-browser BEFORE writing it back and committing (used 2026-07-09 for the 3 new charts + these explainers).

## Standing backlog (rough priority)

Percentile-vs-history markers per card · "changed since last visit" view · AI-scatter model-name hover labels — the generic date+value hover tooltip now ships site-wide (2026-07-07); the remaining nicety is surfacing each point's model NAME on the AI scatter (names already ship in `ai_compute.json`) · affordability ratio · WSTS semiconductor sales · living religiosity series · mobile/PWA polish · generic ballot average (VoteHub has poll_type variants worth probing).

Built 2026-07-03–06 by Claude (Fable) for Landon Bell; hover-tooltip-on-all-charts added 2026-07-07 (Opus); maintained by whichever Claude is on duty.

Monthly border-encounters feed (`border_monthly`, nationwide + Southwest) shipped 2026-07-08 (Opus) via the CBP Public Data Portal CSVs — closing the last backlog feed; the earlier stale `data/border_monthly.json` from a reverted attempt was removed.
