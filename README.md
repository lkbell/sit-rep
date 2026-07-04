# Sit-Rep

Curated dashboards of long-run indicators — markets, economy, debt, housing, energy,
immigration, crime, society, tech/AI, world — refreshed automatically from **primary
sources**. Purpose: ground your perception of the world in actual data trends, not in
whatever dominates the feed this week.

**Live site:** https://lkbell.github.io/sit-rep/

## How it works

- `pipeline/fetch.py` holds the indicator **catalog** (69 charts) and per-source
  adapters (FRED CSV, Treasury FiscalData, CDC Socrata, Zillow Research, Epoch AI,
  Yahoo Finance with FRED/Stooq fallbacks, Real-Time Crime Index, EIA, NY Fed GSCPI,
  GPR, Our World in Data). No API keys required.
- A GitHub Action (`.github/workflows/refresh.yml`) runs **Monday & Thursday mornings**
  (plus on demand, plus on any pipeline change), re-fetches every series, and commits
  compact JSON to `data/`. (Better cron, needs a manual edit since the API can't touch
  workflow files: `23 14 * * 1-5` — weekdays 10:23am ET, after the 8:30am data prints.)
- The site (`index.html` + `app.js`, GitHub Pages) is fully static: it renders
  `data/*.json` with uPlot. Hash-routed pages: an **Overview** landing page with 16
  vital signs (early-warning series first), then one deep-dive page per domain. Every
  card shows latest value, change, a freshness dot, and a link to its primary source.
- **Failures are isolated**: a broken feed marks that one chart red (with the error in
  the dot's tooltip), shows a "N feeds failing" header badge, and keeps its last good
  data; everything else refreshes normally.
- Transforms are **calendar-aware** (YoY compares to the same month a year earlier and
  skips gap months — e.g. the Oct-2025 CPI shutdown gap — rather than counting back 12
  observations).

## Freshness dots

- 🟢 current (within each source's expected release cadence — per-chart `exp` overrides
  for lagged-by-design series) · 🟠 stale · 🔴 feed error ·
  ⚪ curated — slow-moving annual series (fertility, life expectancy, murder rate,
  trust in media, border FY totals…) hand-updated when agencies publish; these age to
  🟠 if an annual update is missed.

## Maintenance notes (for future Claude sessions)

- Add an indicator: add one entry to `CATALOG` in `pipeline/fetch.py` (and a manual
  JSON in `pipeline/manual/` if it's curated). Push to main — the workflow re-runs
  and the site picks it up automatically. No other file needs touching.
- Curated series to update on release: FBI murder rate (fall), CDC fertility/life
  expectancy/marriage (spring–summer), Gallup trust in media (Sept–Oct), CBP border
  FY totals (Oct–Nov).
- Known fragile feeds (best-effort by design): Yahoo Finance (S&P/gold/SOX — FRED and
  Stooq fallbacks wired), EIA .xls, RTCI CSV schema, NY Fed GSCPI (legacy .xls served
  as .xlsx, text dates like "31-Jan-1998" — the tolerant parser handles it and emits
  layout diagnostics into the manifest error if it breaks again).
- A weekly data-only brief (movers + signal thresholds + feed health) runs as a Claude
  scheduled task on Landon's machine, Mondays ~12:30pm ET, reading this site's JSON.
- Backlog: signals/alerting layer on the Overview, presidential approval + generic
  ballot feed, election odds (Polymarket), monthly border-encounters feed (OHSS/CBP),
  affordability ratio, WSTS semiconductor sales, AI-scatter hover labels (model names
  already ship in `ai_compute.json`), living religiosity series, percentile-vs-history
  markers, "changed since last visit" view, PWA/mobile polish.

Built and maintained autonomously by Claude for Landon Bell.
