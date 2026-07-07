# Sit-Rep

Curated dashboards of long-run indicators — markets, economy, debt, housing, energy,
immigration, crime, society, tech/AI, world — refreshed automatically from **primary
sources**. Purpose: ground your perception of the world in actual data trends, not in
whatever dominates the feed this week.

**Live site:** https://lkbell.github.io/sit-rep/

## How it works

- `pipeline/fetch.py` holds the indicator **catalog** (71 charts) and per-source
  adapters (FRED CSV, Treasury FiscalData, CDC Socrata, Zillow Research, Epoch AI,
  Yahoo Finance with FRED/Stooq fallbacks, Real-Time Crime Index, EIA, NY Fed GSCPI,
  GPR, Our World in Data, VoteHub polls, Polymarket). No API keys required.
- A GitHub Action (`.github/workflows/refresh.yml`) runs **every weekday at 14:23 UTC**
  (plus on demand, plus on any pipeline change), re-fetches every series, recomputes
  the early-warning **signals**, and commits compact JSON to `data/`.
- The site (`index.html` + `app.js`, GitHub Pages) is fully static: it renders
  `data/*.json` with uPlot. Hash-routed pages: an **Overview** landing page with 16
  vital signs and a collapsible signals strip, then one deep-dive page per domain.
  Every card shows latest value, change, a freshness dot, an ⓘ explainer, and a link
  to its primary source.
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

## Maintenance

**See MAINTENANCE.md — the full maintainer runbook** (architecture, golden rules, the
signals layer and its kill switches, per-source failure playbook, curated-series
calendar, connector limits, verification checklist, backlog).

Automated upkeep: data refreshes itself weekdays via Actions; a Sunday digest and a
monthly health check run as Claude scheduled tasks on Landon's machine.

Built and maintained autonomously by Claude for Landon Bell.
