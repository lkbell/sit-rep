# Sit-Rep

Curated dashboards of long-run indicators — markets, economy, debt, housing, energy,
immigration, crime, society, tech/AI, world — refreshed automatically from **primary
sources**. Purpose: ground your perception of the world in actual data trends, not in
whatever dominates the feed this week.

**Live site:** https://lkbell.github.io/sit-rep/

## How it works

- `pipeline/fetch.py` holds the indicator **catalog** (~58 charts) and per-source
  adapters (FRED CSV, Treasury FiscalData, CDC Socrata, Zillow Research, Epoch AI,
  Yahoo Finance, Real-Time Crime Index, EIA). No API keys required.
- A GitHub Action (`.github/workflows/refresh.yml`) runs **Monday & Thursday mornings**
  (plus on demand, plus on any pipeline change), re-fetches every series, and commits
  compact JSON to `data/`.
- The site (`index.html` + `app.js`, GitHub Pages) is fully static: it renders
  `data/*.json` with uPlot. Hash-routed pages: an **Overview** landing page with the
  vital signs, then one deep-dive page per domain. Every card shows latest value,
  change, a freshness dot, and a link to its primary source.
- **Failures are isolated**: a broken feed marks that one chart red (with the error in
  the dot's tooltip) and keeps its last good data; everything else refreshes normally.

## Freshness dots

- 🟢 current (within expected release cadence) · 🟠 stale · 🔴 feed error ·
  ⚪ curated — slow-moving annual series (fertility, life expectancy, murder rate,
  trust in media, border FY totals…) hand-updated when agencies publish, each with
  its source linked.

## Maintenance notes (for future Claude sessions)

- Add an indicator: add one entry to `CATALOG` in `pipeline/fetch.py` (and a manual
  JSON in `pipeline/manual/` if it's curated). Push to main — the workflow re-runs
  and the site picks it up automatically. No other file needs touching.
- Curated series to update on release: FBI murder rate (fall), CDC fertility/life
  expectancy/marriage (spring–summer), Gallup trust in media (Sept–Oct), CBP border
  FY totals (Oct–Nov).
- Known fragile feeds (best-effort by design): Yahoo Finance (gold, S&P full history,
  SOX), EIA .xls (crude production), RTCI CSV schema. If one breaks, its dot turns
  red; swap the adapter or fall back to FRED where possible.
- Backlog: monthly border-encounters automated feed (OHSS/CBP portal), presidential
  approval + generic ballot feed, election odds (Polymarket), affordability ratio
  (price/income), GPR geopolitical risk index, semiconductor sales (WSTS), model-name
  hover labels on the AI compute scatter.

Built and maintained autonomously by Claude for Landon Bell.
